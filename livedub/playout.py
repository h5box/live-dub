"""Playout: read the ingest chunks D seconds late, burn subtitles and mix dubs by program time, encode, push.

Only runs when the session sends its output to OBS (the app's own player composes picture, subtitles and dubs itself).
Program time PT = source PTS in seconds. Frames are released by the run's anchor (anchor.json, written by ingest at
the first chunk; made here from the first decoded frame if there is none): a frame with PT p leaves at wall time
T0 + D + (p - p0), the same moment the app's player shows it. Subtitles and dubs are looked up by PT, so they land on
the right frame and sample however late the models delivered them, as long as the event file has them before release.

Events come from a JSONL file that the processor appends to (one object per line):
  {"pt0": 1569.0, "pt1": 1575.5, "zh": "...", "en": "...", "dub": ".../clips/dub_00012.wav" | null, "gain": 1.0}
An event whose pt0 equals an earlier one's replaces it (the processor writes subtitles first, the dub later).
Output is clock-driven: tick k shows PT p0 + k / 29.97 and leaves at T0 + D + k / 29.97, with the newest source frame
at or before that PT and the source audio of that PT (silence where the source has none); after 2 s without a new
source frame a slate replaces the frozen picture. Output timestamps come from the tick counter, never from the source,
so a source that stalls, drops audio or changes frame rate cannot make the stream stop or drift.
The original audio is ducked by -15 dB under every dub (250 ms raised-cosine attack finishing at pt0, 1 s release,
kept down across gaps < 1.5 s); a dub never runs into the next event's onset.

usage: python -m livedub.playout BUF_DIR EVENTS_JSONL OUT [--delay 30] [--vcodec auto] [--tag "AI 翻译配音"] [--gc]
OUT is an rtmp:// URL (flv) or a file path.
"""
import argparse
import bisect
import io
import json
import threading
import time
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from . import settings, subs

SR = 48000
DUCK_DB, ATTACK, RELEASE, BRIDGE, HOLD = -15.0, 0.25, 1.0, 1.5, 0.15
DUB_RMS = 0.08  # about -22 dBFS until the processor supplies a per-sentence gain


class ChunkReader(io.RawIOBase):
    """Concatenates seg_NNNNNN.ts as ingest writes them; a chunk counts as complete once the next one (or END) exists.
    timeout=None waits for the next chunk forever (live: a stalled source is the playout's slate, not an EOF).
    gc deletes chunks GC_KEEP behind the one being read (the processor reads the same chunks, normally far ahead)."""

    GC_KEEP = 60

    def __init__(self, buf, timeout=None, gc=False, start=0, stop=None):
        self.buf, self.i, self.f, self.timeout, self.gc = Path(buf), start, None, timeout, gc
        self.stop = stop

    def readable(self):
        return True

    def _path(self, i):
        return self.buf / f"seg_{i:06d}.ts"

    def readinto(self, b):
        waited = 0.0
        while True:
            if self.stop is not None and self.stop.is_set():
                if self.f is not None:
                    self.f.close(); self.f = None
                return 0
            if self.f is None:
                ended = (self.buf / "END").exists()
                if self._path(self.i).exists() and (self._path(self.i + 1).exists() or ended):
                    self.f = open(self._path(self.i), "rb")
                elif ended and not self._path(self.i).exists():
                    return 0
                elif self.timeout is not None and waited > self.timeout:
                    return 0
                else:
                    time.sleep(0.05); waited += 0.05
                    continue
            n = self.f.readinto(b)
            if n:
                return n
            self.f.close(); self.f = None; self.i += 1
            if self.gc and self.i > self.GC_KEEP:
                self._path(self.i - 1 - self.GC_KEEP).unlink(missing_ok=True)


def trim_silence(x, keep=0.03):
    hop = SR // 100
    if len(x) < hop * 3:
        return x
    db = 20 * np.log10(np.sqrt((x[: len(x) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1)) + 1e-9)
    on = np.flatnonzero(db > db.max() - 30)
    return x[max(0, on[0] * hop - int(keep * SR)): min(len(x), (on[-1] + 1) * hop + int(keep * SR))]


def load_dub(path):
    with av.open(str(path)) as c:
        rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        parts = [f.to_ndarray().reshape(-1) for fr in c.decode(audio=0) for f in rs.resample(fr)]
        parts += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    x = trim_silence(np.concatenate(parts).astype(np.float32))
    return x * (DUB_RMS / max(float(np.sqrt(np.mean(x ** 2))), 1e-4))


class Events:
    """Tails the events JSONL; keeps events sorted by pt0 with their loaded dub audio."""

    def __init__(self, path):
        self.path, self.pos, self.lock = Path(path), 0, threading.Lock()
        self.ev: list[dict] = []
        self.starts: list[float] = []

    def poll(self):
        if not self.path.exists():
            return
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read()
        end = data.rfind(b"\n") + 1
        self.pos += end
        for line in data[:end].decode("utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("dub"):
                try:
                    e["audio"] = load_dub(e["dub"]) * float(e.get("gain", 1.0))
                except Exception as ex:  # a broken dub file degrades to subtitles only
                    print("dub load failed", e["dub"], ex, flush=True)
            with self.lock:
                k = bisect.bisect(self.starts, e["pt0"])
                if k and self.starts[k - 1] == e["pt0"]:  # same unit again, now with its dub
                    self.ev[k - 1] = e
                else:
                    self.starts.insert(k, e["pt0"]); self.ev.insert(k, e)

    def run(self, stop):
        while not stop.is_set():
            self.poll(); time.sleep(0.5)

    def at(self, p):
        """The latest-starting event covering PT p (subtitles)."""
        with self.lock:
            k = bisect.bisect(self.starts, p) - 1
            while k >= 0 and k >= len(self.ev) - 400:
                if self.ev[k]["pt0"] <= p < self.ev[k]["pt1"]:
                    return self.ev[k]
                if self.ev[k]["pt1"] < p - 30:
                    break
                k -= 1
        return None

    def forget(self, pt):
        """Drop the decoded dub audio of events that ended before PT pt (hours of dubs would not fit in memory)."""
        with self.lock:
            for e in self.ev:
                if e["pt1"] < pt:
                    e.pop("audio", None)

    def around(self, t0, t1):
        with self.lock:
            k = bisect.bisect(self.starts, t1 + ATTACK)
            out = []
            for j in range(k - 1, -1, -1):
                e = self.ev[j]
                if e["pt0"] + e.get("span", 60) < t0 - RELEASE - BRIDGE - 60:
                    break
                out.append((j, e))
            nxt = {j: (self.ev[j + 1]["pt0"] if j + 1 < len(self.ev) else None) for j, _ in out}
        return out[::-1], nxt


def mix_audio(pcm, t0, events):
    """pcm: (2, n) float32 at SR starting at PT t0. Ducks the original and adds dubs, in place."""
    n = pcm.shape[1]
    t = t0 + np.arange(n) / SR
    evs, nxt = events.around(t0, t0 + n / SR)
    regions = []
    dub = np.zeros(n, np.float32)
    for j, e in evs:
        a = e.get("audio")
        if a is None:
            continue
        end = e["pt0"] + len(a) / SR
        if nxt[j] is not None:
            end = min(end, nxt[j] - 0.05)
        regions.append([e["pt0"], max(e["pt1"], end)])
        idx = np.round((t - e["pt0"]) * SR).astype(np.int64)
        m = (idx >= 0) & (idx < len(a)) & (t < end)
        if m.any():
            seg = a[idx[m]]
            fade = np.clip((end - t[m]) / 0.05, 0, 1)  # 50 ms fade where a dub is cut short
            dub[m] += seg * fade
    merged = []
    for s, e in sorted(regions):
        if merged and s - merged[-1][1] < BRIDGE:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    gain_db = np.zeros(n, np.float32)
    for s, e in merged:
        down = np.clip((t - (s - ATTACK)) / ATTACK, 0, 1)
        up = np.clip(1 - (t - (e + HOLD)) / RELEASE, 0, 1)
        w = np.minimum(0.5 - 0.5 * np.cos(np.pi * down), 0.5 - 0.5 * np.cos(np.pi * up))
        gain_db = np.minimum(gain_db, DUCK_DB * w)
    pcm *= 10 ** (gain_db / 20)
    pcm += dub
    np.clip(pcm, -0.98, 0.98, out=pcm)


class Source:
    """Decodes the chunks ahead of release: RGB video frames go to a short queue, 48 kHz stereo audio to a ring indexed
    by output sample (PT p <-> sample round((p - p0) * SR)). Frames are held back until LEAD seconds before their
    release, so only about a second of decoded video is ever in memory (the D seconds wait as compressed chunks)."""

    LEAD = 1.0
    RING = SR * 20

    def __init__(self, buf, events_path, delay, gc):
        self.reader = ChunkReader(buf, timeout=None, gc=gc)
        self.events_path, self.delay = Path(events_path), delay
        self.p0 = self.T0 = None
        self.video, self.lock = [], threading.Lock()
        self.ring = np.zeros((2, self.RING), np.float32)
        self.stamp = np.full(self.RING, -1, np.int64)  # which output sample each ring slot holds
        self.last_pt, self.ended, self.started = None, False, threading.Event()
        self.audio_logged = False

    def run(self):
        try:
            src = av.open(io.BufferedReader(self.reader, buffer_size=1 << 20), format="mpegts")
            vin, ain = src.streams.video[0], src.streams.audio[0]
            rs = av.AudioResampler(format="fltp", layout="stereo", rate=SR)
            vdur = float(1 / vin.average_rate) if vin.average_rate else 1001 / 30000
            for frame in src.decode(vin, ain):
                if frame.pts is None:  # the decoder leaves a few frames without pts; video can be inferred
                    if not isinstance(frame, av.VideoFrame) or self.last_pt is None:
                        continue
                    p = self.last_pt + vdur
                else:
                    p = float(frame.pts * frame.time_base)
                if self.p0 is None and not isinstance(frame, av.VideoFrame):
                    continue  # the output grid is phased on the first video frame; earlier audio is dropped
                if self.p0 is None:
                    self.p0, self.T0 = p, self.anchor_T0(p)
                    print(f"first frame PT {p:.3f}; release delay {self.delay}s", flush=True)
                    self.started.set()
                dt = self.T0 + self.delay + (p - self.p0) - self.LEAD - time.time()
                if dt > 0:
                    time.sleep(dt)
                if isinstance(frame, av.VideoFrame):
                    rgb = frame.to_ndarray(format="rgb24")
                    if rgb.shape[:2] != (1080, 1920):
                        rgb = frame.reformat(width=1920, height=1080, format="rgb24").to_ndarray()
                    with self.lock:
                        self.video.append((p, rgb))
                    self.last_pt = p
                else:
                    for rf in rs.resample(frame):
                        x = rf.to_ndarray().astype(np.float32)
                        q = float(rf.pts * rf.time_base) if rf.pts is not None else p
                        if not self.audio_logged:  # A/V phase at start (a live run once showed a constant 0.57 s skew)
                            self.audio_logged = True
                            print(f"first audio: frame PT {p:.3f}, resampled PT {q:.3f}, p0 {self.p0:.3f}", flush=True)
                        s0 = int(round((q - self.p0) * SR))
                        idx = np.arange(s0, s0 + x.shape[1])
                        ok = idx >= 0
                        slot = idx[ok] % self.RING
                        self.ring[:, slot] = x[:, ok]; self.stamp[slot] = idx[ok]
        except Exception as ex:
            print("source ended with", repr(ex), flush=True)
        self.ended = True
        self.started.set()

    def anchor_T0(self, p):
        """T0 for a grid starting at the first frame's PT p: from the run's anchor (ingest writes it when the first
        chunk closes, about when this frame decodes), else made here and written for the processor."""
        path = self.events_path.with_name("anchor.json")
        for _ in range(50):
            try:
                a = json.loads(path.read_text(encoding="utf-8"))
                return a["T0"] + (p - a["p0"])
            except (OSError, ValueError, KeyError):
                time.sleep(0.1)
        T0 = time.time()
        path.write_text(json.dumps({"T0": T0, "p0": p, "delay": self.delay}))
        return T0

    def frame_at(self, p):
        """The latest decoded frame with PT <= p (plus half a frame of slack); older ones are dropped."""
        with self.lock:
            k = 0
            while k < len(self.video) and self.video[k][0] <= p:
                k += 1
            if k == 0:
                return None
            pt, rgb = self.video[k - 1]
            del self.video[:k - 1]
            return pt, rgb

    def audio(self, s0, n):
        idx = np.arange(s0, s0 + n)
        slot = idx % self.RING
        x = self.ring[:, slot].copy()
        x[:, self.stamp[slot] != idx] = 0.0   # never written (source gap) -> silence
        return x


def nvenc_works():
    """Whether h264_nvenc can actually encode here (it is in PyAV's build even without an NVIDIA GPU)."""
    try:
        c = av.CodecContext.create("h264_nvenc", "w")
        c.width, c.height, c.pix_fmt, c.time_base = 256, 256, "yuv420p", Fraction(1, 30)
        c.open()
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("buf"); ap.add_argument("events"); ap.add_argument("out")
    ap.add_argument("--delay", type=float, default=30.0)
    ap.add_argument("--vcodec", default=settings.get()["obs"]["vcodec"],
                    help="auto (h264_nvenc when the GPU can, else libx264), or any encoder PyAV knows")
    ap.add_argument("--tag", default="AI 翻译配音")
    ap.add_argument("--bitrate", default="8M")
    ap.add_argument("--gc", action="store_true", help="delete chunks once played (live runs)")
    ap.add_argument("--sub-bottom", type=int, help="subtitle bottom margin in px (default 40)")
    ap.add_argument("--sub-box", type=float, help="subtitle backing box opacity 0..1 (default none)")
    ap.add_argument("--tag-corner", choices=["tl", "tr"])
    a = ap.parse_args()

    events = Events(a.events)
    events.poll()
    stop = threading.Event()
    threading.Thread(target=events.run, args=(stop,), daemon=True).start()

    source = Source(a.buf, a.events, a.delay, a.gc)
    threading.Thread(target=source.run, daemon=True).start()
    source.started.wait()
    if source.p0 is None:
        print("no input", flush=True)
        return
    p0, T0 = source.p0, source.T0
    fps = Fraction(30000, 1001)

    vcodec = a.vcodec if a.vcodec != "auto" else ("h264_nvenc" if nvenc_works() else "libx264")
    print(f"video encoder {vcodec}", flush=True)
    out = av.open(a.out, "w", format="flv" if a.out.startswith("rtmp") else None)
    vs = out.add_stream(vcodec, rate=fps)
    vs.width, vs.height, vs.pix_fmt = 1920, 1080, "yuv420p"
    vs.codec_context.time_base = 1 / fps
    if vcodec == "h264_nvenc":
        vs.options = {"preset": "p5", "tune": "ll", "rc": "cbr", "b": a.bitrate, "maxrate": a.bitrate,
                      "bufsize": "16M", "g": "60", "bf": "0"}
    else:
        vs.options = {"preset": "veryfast", "tune": "zerolatency", "b": a.bitrate, "g": "60"}
    aus = out.add_stream("aac", rate=SR, layout="stereo")
    aus.bit_rate = 192000
    fifo = av.AudioFifo()

    slate = np.zeros((1080, 1920, 3), np.uint8)
    subs.blend(slate, subs.render("信号中断，稍后恢复", "Source interrupted, back soon"))
    subs.configure(a.sub_bottom, a.sub_box, a.tag_corner)
    tag = subs.render_tag(a.tag) if a.tag else None
    last = None                     # (pt, rgb) of the newest source frame shown
    k = s_out = a_pts = 0           # output frame / audio sample counters: the output clock, never the source's
    last_log, late_max, shown, dups, slates, gap_since = 0.0, 0.0, 0, 0, 0, None
    half = 0.5 / float(fps)
    try:
        while True:
            p = p0 + k / float(fps)                     # tick k shows PT p and leaves at T0 + D + (p - p0)
            dt = T0 + a.delay + (p - p0) - time.time()
            if dt > 0:
                time.sleep(dt)
            else:
                late_max = max(late_max, -dt)
            f = source.frame_at(p + half)
            if f is not None and (last is None or f[0] != last[0]):
                last = f
            elif last is not None:
                dups += 1
            if source.ended and (source.last_pt is None or p > source.last_pt + 1.0):
                break
            if last is None or p - last[0] > 2.0:       # nothing new for 2 s: slate instead of a frozen picture
                rgb = slate.copy(); slates += 1
                gap_since = gap_since or time.time()
            else:
                if gap_since:
                    print(f"source back after {time.time() - gap_since:.1f}s of gap", flush=True); gap_since = None
                rgb = last[1].copy()
                e = events.at(p)
                if e:
                    subs.blend(rgb, subs.render(e.get("zh") or "", e.get("en") or "")); shown += 1
            if tag is not None:
                subs.blend(rgb, tag)
            nf = av.VideoFrame.from_ndarray(rgb, format="rgb24").reformat(format="yuv420p")
            nf.pts = k
            for pkt in vs.encode(nf):
                out.mux(pkt)
            # audio for this tick: samples [s_out, s1) of the output = PT p0 + s / SR
            s1 = int(round((k + 1) * SR / float(fps)))
            pcm = source.audio(s_out, s1 - s_out)
            mix_audio(pcm, p0 + s_out / SR, events)
            of = av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm), format="fltp", layout="stereo")
            of.sample_rate = SR
            fifo.write(of)
            while fifo.samples >= 1024:
                af = fifo.read(1024)
                af.pts, af.time_base = a_pts, Fraction(1, SR)
                a_pts += 1024
                for pkt in aus.encode(af):
                    out.mux(pkt)
            s_out, k = s1, k + 1
            now = time.time()
            if now - last_log > 5:
                events.forget(p - 120)
                print(f"[{time.strftime('%H:%M:%S')}] PT {p:.2f} (+{p - p0:.1f}s) frames {k} sub-frames {shown} "
                      f"dups {dups} slate {slates} events {len(events.ev)} max-late {late_max * 1000:.0f} ms", flush=True)
                last_log, late_max = now, 0.0
    finally:
        stop.set()
        for pkt in vs.encode(None):
            out.mux(pkt)
        for pkt in aus.encode(None):
            out.mux(pkt)
        out.close()
        print("playout ended", flush=True)


if __name__ == "__main__":
    main()
