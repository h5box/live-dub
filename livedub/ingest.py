"""Ingest: read a source and cut it into ~1 s MPEG-TS chunks whose timestamps are the program time PT.

PT is what every other part keys on (subtitle and dub events, the playout clock), so it must only ever move forward.
- File source ("fake live"): -re paces it like a broadcast and -copyts keeps the file's own timestamps as PT, so
  bench tools can compare against the file.
- Live source (--live; an http(s) media URL, or a page that yt-dlp resolves to its HLS manifest): no -copyts,
  so ffmpeg starts PT near 0 and folds the source's timestamp jumps (discontinuities, encoder restarts) into a
  continuous timeline. When ffmpeg exits (network, expired manifest) it is restarted after a short pause; the new
  run's PT is offset to continue from the last chunk's end plus the wall time lost, and chunk numbering continues.
  The playout shows a slate over the gap. Every restart is logged.
Chunks are cut at keyframes (stream copy), so their length follows the source GOP. A file whose codecs a browser
cannot play from MPEG-TS (the app's player reads these chunks as HLS) is converted on the way: audio other than AAC/MP3
(e.g. Opus) to AAC, video other than H.264 to H.264.
When the first chunk closes, its start PT p0 and the wall time T0 go to ANCHOR (default BUF/../anchor.json) with the
delay D: every viewer of the run shares it (PT p is shown at T0 + D + (p - p0)), the processor sets dub deadlines by
it. A live run keeps the last --keep-s seconds of chunks (the app can rewind that far); a file run keeps all of them.

yt-dlp and streamlink are this project's Python dependencies; ffmpeg / ffprobe come from settings (tools).

usage: python -m livedub.ingest SRC BUF_DIR [--ss SEC] [--t SEC] [--live] [--cookies FILE] [--re] [--delay D]
       python -m livedub.ingest --src-config RUN/config.json BUF_DIR ...   (src, src_ss, cookies, max_height from it)
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import settings

FFMPEG, FFPROBE = settings.ffmpeg(), settings.ffprobe()


def best_variant(url, max_h=1080):
    """An HLS master playlist -> its highest-bandwidth variant of at most max_h lines; a media playlist passes through."""
    text = urllib.request.urlopen(url, timeout=15).read().decode("utf-8", "replace")
    best, lines = None, text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            bw = int((re.search(r"BANDWIDTH=(\d+)", line) or [0, 0])[1])
            h = int((re.search(r"RESOLUTION=\d+x(\d+)", line) or [0, 0])[1])
            nxt = next((l for l in lines[i + 1:] if l and not l.startswith("#")), None)
            if nxt and h <= max_h and (best is None or (h, bw) > best[:2]):
                best = (h, bw, urllib.parse.urljoin(url, nxt))
    if best:
        print(f"HLS variant {best[0]}p {best[1] // 1000} kb/s", flush=True)
    return best[2] if best else url


def yt_dlp_argv(cookies=None, cookies_from_browser=None):
    """yt-dlp from this environment; YouTube needs a JavaScript runtime (deno or node) for its player challenges."""
    cmd = settings.python_argv("yt_dlp") + ["--no-warnings"]
    js = settings.js_runtime()
    if js:
        cmd += ["--js-runtimes", js, "--remote-components", "ejs:github"]
    if cookies:
        cmd += ["--cookies", cookies]
    elif cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    return cmd


def resolve(src, cookies, max_h=1080, cookies_from_browser=None):
    """A page URL (YouTube, Twitch, ...) -> (direct media URL via yt-dlp, is it live); direct media URLs pass through.
    Not live = a recording behind a URL (a VOD playlist, an .mp4, a past video): it is read in real time like a file."""
    if not src.startswith("http"):
        return src, False
    if src.split("?")[0].endswith(".m3u8"):
        url = best_variant(src, max_h)
        try:
            vod = "#EXT-X-ENDLIST" in urllib.request.urlopen(url, timeout=15).read().decode("utf-8", "replace")
        except Exception:
            vod = False
        return url, not vod
    if src.split("?")[0].endswith((".ts", ".flv", ".mp4", ".mkv", ".webm", ".mov")):
        return src, False
    cmd = yt_dlp_argv(cookies, cookies_from_browser) + [
        "--print", "is_live", "--print", "urls", "-f", f"best[height<={max_h}][protocol*=m3u8]/best[height<={max_h}]/best"]
    r = subprocess.run(cmd + [src], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    lines = r.stdout.split()
    urls = [u for u in lines if u.startswith("http")]
    if not urls:
        # its ERROR line says why; the help text yt-dlp appends would push the reason out of a plain tail
        err = [x for x in r.stderr.splitlines() if x.startswith("ERROR:")]
        raise RuntimeError("yt-dlp gave no URL: " + (err[-1] if err else r.stderr.strip()[-300:])[:300])
    return urls[0], "True" in lines


def stamp(stream, who):
    """Copy a child's stderr to ours, each line prefixed with wall time (ffmpeg's warnings carry none)."""
    for line in stream:
        text = line.decode("utf-8", "replace").rstrip()
        print(f"[{time.strftime('%H:%M:%S')}] {who}: {text}", file=sys.stderr, flush=True)


def watch_arrivals(part, log, stop):
    """Append "wall,chunk,pt_end" to log as each chunk of this run closes (ffmpeg adds its row to part at that moment),
    so the source's delivery timeline is observed, not inferred from the processor's lag."""
    seen = 0
    while True:
        done = stop.is_set()
        rows = [r for r in csv.reader(part.read_text().splitlines()) if len(r) >= 3] if part.exists() else []
        if len(rows) > seen:
            with open(log, "a") as f:
                for r in rows[seen:]:
                    f.write(f"{time.time():.3f},{r[0]},{r[2]}\n")
            seen = len(rows)
        if done:
            return
        time.sleep(0.25)


def chunks(buf):
    """(count, end PT of the last chunk) from ffmpeg's segment list."""
    f = buf / "list.csv"
    if not f.exists():
        return 0, None
    rows = [r for r in csv.reader(f.read_text().splitlines()) if len(r) >= 3]
    return len(rows), (float(rows[-1][2]) if rows else None)


def listed(buf):
    """Every closed chunk so far as (file, start PT, end PT): list.csv (file runs, finished live runs) and the live
    run in progress (list_runN.csv, merged into list.csv when the run ends)."""
    rows = []
    for f in [buf / "list.csv"] + sorted(buf.glob("list_run*.csv"), key=lambda x: int(x.stem[8:])):
        if f.exists():
            rows += [(r[0], float(r[1]), float(r[2])) for r in csv.reader(f.read_text().splitlines()) if len(r) >= 3]
    return sorted(set(rows), key=lambda r: r[0])


def first_pt(seg):
    """The PT a chunk starts at, from the chunk itself: the segment list says 0 for the first chunk of every ffmpeg run."""
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", str(seg)],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return float(out)
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def anchor_when_first_chunk(buf, path, delay, stop):
    """Write the run's anchor as soon as the first chunk is listed (see the module doc)."""
    while True:
        done = stop.is_set()
        rows = listed(buf)
        if rows:
            T0 = time.time()
            p0 = first_pt(buf / rows[0][0])
            p0 = rows[0][2] - 1.0 if p0 is None else p0   # unreadable: about a chunk before its end
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"T0": T0, "p0": p0, "delay": delay}))
            tmp.replace(path)
            print(f"[{time.strftime('%H:%M:%S')}] anchor: first chunk at PT {p0:.3f}", flush=True)
            return
        if done:
            return
        time.sleep(0.1)


def gc_chunks(buf, keep_s, stop):
    """Live runs: delete chunks that ended more than keep_s seconds before the newest one."""
    while not stop.wait(10):
        rows = listed(buf)
        if not rows:
            continue
        edge = rows[-1][2]
        for name, _, end in rows:
            if end < edge - keep_s:
                (buf / name).unlink(missing_ok=True)


def browser_codecs(src):
    """ffmpeg codec options for a file source: copy what a browser plays from MPEG-TS (H.264, AAC/MP3), convert the rest."""
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "csv=p=0",
                              src], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ["-c", "copy"]
    codecs = {}
    for line in out.split():          # "codec_name,codec_type" per stream; the first stream of each type is mapped
        name, _, kind = line.partition(",")
        codecs.setdefault(kind, name)
    v = ["-c:v", "copy"] if codecs.get("video") == "h264" else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    a = ["-c:a", "copy"] if codecs.get("audio") in ("aac", "mp3") else ["-c:a", "aac", "-b:a", "192k"]
    if v[1] != "copy" or a[1] != "copy":
        print(f"converting for playback: video {codecs.get('video')} -> {'copy' if v[1] == 'copy' else 'h264'}, "
              f"audio {codecs.get('audio')} -> {'copy' if a[1] == 'copy' else 'aac'}", flush=True)
    return v + a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?"); ap.add_argument("buf", nargs="?")
    ap.add_argument("--ss", type=float, default=0.0, help="start this many seconds into a file source")
    ap.add_argument("--t", type=float, default=0.0, help="stop after this many seconds (0 = to the end)")
    ap.add_argument("--live", action="store_true", help="live source: continuous PT from 0, restart on failure")
    ap.add_argument("--re", action="store_true", help="with --live and a file: pace it in real time (tests)")
    ap.add_argument("--ahead", action="store_true", help="process recordings ahead of playback")
    ap.add_argument("--cookies", default=None)
    ap.add_argument("--cookies-from-browser", default=None)
    ap.add_argument("--max-height", type=int, default=1080,
                    help="highest variant to pull (720 halves the bytes of a 1080p60 YouTube stream; playout scales to 1080)")
    ap.add_argument("--keep", default=None,
                    help="live: also write each run's stream, with the chunks' timestamps, to KEEP/keep_runN.ts (drift checks)")
    ap.add_argument("--fetcher", choices=["streamlink", "ffmpeg"], default="streamlink",
                    help="who downloads a live HLS source (ffmpeg always does the cutting)")
    ap.add_argument("--delay", type=float, default=30.0, help="the run's delay D, written into the anchor")
    ap.add_argument("--anchor", default=None, help="anchor file (default BUF/../anchor.json)")
    ap.add_argument("--keep-s", type=float, default=1800.0, help="live: keep this many seconds of chunks (0 = all)")
    ap.add_argument("--src-config", default=None,
                    help="take src (and src_ss, cookies, max_height) from this run config; the one positional is BUF")
    a = ap.parse_args()
    if a.src_config:
        cfg = json.loads(Path(a.src_config).read_text(encoding="utf-8"))
        a.buf = a.buf or a.src
        a.src = cfg["src"]
        a.ss = a.ss or float(cfg.get("src_ss", 0))
        a.cookies = a.cookies or cfg.get("cookies")
        a.cookies_from_browser = a.cookies_from_browser or cfg.get("cookies_from_browser")
        a.max_height = int(cfg.get("max_height", a.max_height))
    if not a.src or not a.buf:
        ap.error("need SRC and BUF (or --src-config CONFIG BUF)")
    buf = Path(a.buf)
    if buf.exists():
        shutil.rmtree(buf)
    buf.mkdir(parents=True)
    stop_all = threading.Event()
    anchor = Path(a.anchor) if a.anchor else buf.parent / "anchor.json"
    anchor.unlink(missing_ok=True)
    anchor_worker = threading.Thread(target=anchor_when_first_chunk, args=(buf, anchor, a.delay, stop_all), daemon=True)
    anchor_worker.start()
    seg = ["-muxdelay", "0", "-muxpreload", "0",
           "-f", "segment", "-segment_time", "1", "-segment_format", "mpegts", "-reset_timestamps", "0",
           "-segment_list_type", "csv", "-segment_list_flags", "live"]
    out = ["-map", "0:v:0", "-map", "0:a:0", "-c", "copy"] + seg
    base = [FFMPEG, "-hide_banner", "-loglevel", "level+warning"]
    if not a.live and not a.src.startswith("http"):
        (buf.parent / "source.json").write_text(json.dumps({"is_live": False}), encoding="utf-8")
        cmd = base + ([] if a.ahead else ["-re"])
        if a.ss:
            cmd += ["-ss", str(a.ss)]
        if a.t:  # input option: with -copyts an output -t would count from PT 0, not from --ss
            cmd += ["-t", str(a.t)]
        cmd += ["-copyts", "-i", a.src, "-map", "0:v:0", "-map", "0:a:0"] + browser_codecs(a.src) + seg + [
            "-segment_list", str(buf / "list.csv"), str(buf / "seg_%06d.ts")]
        rc = subprocess.run(cmd).returncode
        (buf / "END").write_text(str(rc))  # readers treat the last chunk as complete once this exists
        stop_all.set()
        anchor_worker.join()
        return

    t_start, run, rc = time.time(), 0, 0
    lost_at = None
    while True:
        left = a.t - (time.time() - t_start) if a.t else None
        if left is not None and left <= 1:
            break
        n, end = chunks(buf)
        # ffmpeg (no -copyts) starts each run near PT 0; shift it to follow on from the last chunk, plus the gap
        offset = 0.0 if end is None else end + (time.time() - lost_at if lost_at else 0.0)
        run += 1
        try:
            url, is_live = resolve(a.src, a.cookies, a.max_height, a.cookies_from_browser)
            (buf.parent / "source.json").write_text(json.dumps({"is_live": is_live}), encoding="utf-8")
            if run == 1 and is_live and a.keep_s:
                threading.Thread(target=gc_chunks, args=(buf, a.keep_s, stop_all), daemon=True).start()
        except Exception as ex:
            print(f"[{time.strftime('%H:%M:%S')}] resolve failed: {ex}", flush=True)
            lost_at = lost_at or time.time(); time.sleep(10); continue
        hls = url.startswith("http") and (".m3u8" in url or "hls_playlist" in url)
        sl = None
        if hls and a.fetcher == "streamlink":
            # ffmpeg's HLS reader fetches segments one after another: one stalled request (seen on YouTube through a
            # proxy: TLS keepalive resets, ~10 s to first byte) puts it behind the 30 s playlist window and the
            # segments it missed are skipped. streamlink fetches ahead in parallel and retries; ffmpeg just cuts.
            sl = subprocess.Popen(settings.python_argv("streamlink") + ["--loglevel", "warning", "--stream-segment-threads", "3",
                                   "--stream-segment-attempts", "5", "--stream-segment-timeout", "8",
                                   "--hls-live-edge", "3", "--hls-playlist-reload-attempts", "5",
                                   "-O", "hls://" + url, "best"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            threading.Thread(target=stamp, args=(sl.stderr, "streamlink"), daemon=True).start()
            cmd = list(base)
        elif url.startswith("http"):
            cmd = base + ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                          "-rw_timeout", "15000000"]
        else:
            cmd = list(base)
        if a.re or (url.startswith("http") and not is_live and not a.ahead):
            cmd += ["-re"]
        if a.ss and run == 1 and not url.startswith("http"):
            cmd += ["-ss", str(a.ss)]
        if left is not None:
            cmd += ["-t", f"{left:.3f}"]
        cmd += ["-i", "pipe:0" if sl else url, "-output_ts_offset", f"{offset:.6f}"] + out + [
            "-segment_start_number", str(n), "-segment_list", str(buf / f"list_run{run}.csv"),
            str(buf / "seg_%06d.ts")]
        if a.keep:
            Path(a.keep).mkdir(parents=True, exist_ok=True)
            cmd += ["-output_ts_offset", f"{offset:.6f}", "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                    "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", str(Path(a.keep) / f"keep_run{run}.ts")]
        stop = threading.Event()
        watcher = threading.Thread(target=watch_arrivals, args=(buf / f"list_run{run}.csv", buf / "arrivals.csv", stop),
                                   daemon=True)
        watcher.start()
        print(f"[{time.strftime('%H:%M:%S')}] run {run}: chunk {n}, PT offset {offset:.3f}", flush=True)
        # ffmpeg's own warnings (e.g. HLS segments skipped) carry no time: stamp each line with wall time
        p = subprocess.Popen(cmd, stdin=sl.stdout if sl else None, stderr=subprocess.PIPE)
        if sl:
            sl.stdout.close()  # ffmpeg holds the pipe now; streamlink sees it close if ffmpeg exits
        stamp(p.stderr, "ffmpeg")
        rc = p.wait()
        stop.set(); watcher.join()
        if sl:
            sl.terminate(); sl.wait()
        # merge this run's list into list.csv (the one playout/tools and the next run's offset read)
        part = buf / f"list_run{run}.csv"
        if part.exists():
            with open(buf / "list.csv", "a") as f:
                f.write(part.read_text())
        lost_at = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] run {run} ended rc={rc}", flush=True)
        if (not a.src.startswith("http") or not is_live) and rc == 0:
            break  # a file or a recording behind a URL that played to the end
        if rc == 0 and left is not None and (chunks(buf)[1] or 0.0) >= offset + left - 1.0:
            break  # ffmpeg reached its -t: the run's time is up (a live edge fetched in a burst ends it before the wall clock)
        time.sleep(3)
    (buf / "END").write_text(str(rc))
    stop_all.set()
    anchor_worker.join()


if __name__ == "__main__":
    main()
