"""Stand-in for livedub.server when working on the UI without the services or the GPU: replays a finished run on its
own clock as if it were happening now. Presets, models and past sessions come from the real code (configs/,
outputs/live/); the picture is a test pattern with the program time burnt in (made once with ffmpeg, cached in the
run's dev_media/), and every dub is a short tone of the dub's length, so you can see and hear what lines up.

  uv run python app/dev/mock_server.py [--run outputs/live/<name>] [--scenario live] [--offset 600] [--port 8099]
  then in app/:  $env:VITE_API = "http://127.0.0.1:8099"; pnpm dev      (http://localhost:1420)

--run defaults to the newest run that has records. --media ts (H.264/AAC in MPEG-TS, what ingest makes; plays in
Edge/Chrome/WebView2) or fmp4 (VP9/Opus fMP4, for open-source Chromium builds without H.264, e.g. Playwright's).
Scenarios:
  live      running, --offset seconds into the run; the rest of its records and picture arrive when they did
  ended     the run is over, services still up (what the backend shows after a session or a restart)
  fresh     no session yet, every service down, a GPU warning, the environment check of this machine as it is
  starting  services coming up; turns live after 6 s
  failed    the last start failed
  blocked   running, but the link cannot be opened (YouTube's sign-in check): no picture, the reason on the player
开始 in the UI starts a new session (new name) that replays the run from its first frame; 停止 ends it.
"""
import argparse
import io
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from livedub.server import session as real   # noqa: E402  (configs, models, past runs, playlist: the real code)
from livedub.server import services as real_svc   # noqa: E402
from livedub import doctor as real_doctor   # noqa: E402


def newest_run() -> Path:
    for rj in sorted(real.RUNS.glob("*/run.json"), reverse=True):
        if (rj.parent / "anchor.json").exists() and (rj.parent / "timing.jsonl").exists():
            return rj.parent
    sys.exit(f"no finished run with records under {real.RUNS}; pass --run")


ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("--run", type=Path)
ap.add_argument("--scenario", default="live", choices=("live", "ended", "fresh", "starting", "failed", "blocked"))
ap.add_argument("--offset", type=float, default=600, help="live: seconds into the run")
ap.add_argument("--media", default="ts", choices=("ts", "fmp4"))
ap.add_argument("--port", type=int, default=8099)
A = ap.parse_args()
RUN_DIR = (A.run or newest_run()).resolve()


def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


RUN = json.loads((RUN_DIR / "run.json").read_text(encoding="utf-8"))
ANCHOR = json.loads((RUN_DIR / "anchor.json").read_text(encoding="utf-8"))
EVENTS, TIMING = jl(RUN_DIR / "events.jsonl"), jl(RUN_DIR / "timing.jsonl")
T0, P0, DELAY = ANCHOR["T0"], ANCHOR["p0"], ANCHOR["delay"]
UNITS = {r["pt0"]: r for r in TIMING if "pt1" in r}
END_PT = max(r["pt1"] for r in UNITS.values()) + 5


def air(pt: float) -> float:
    return T0 + DELAY + (pt - P0)


# when each record appeared on the original run's clock: subtitles when written, dubs when their clip was ready
STAMPED = []
for e in EVENTS:
    if e["pt0"] not in UNITS:
        continue
    if e.get("dub"):
        d = next((r for r in TIMING if r.get("pt0") == e["pt0"] and r.get("dub") == "ok"), None)
        t = air(e["pt0"]) - 1.0 - d["left_s"] if d else UNITS[e["pt0"]]["written"] + 2
    else:
        t = UNITS[e["pt0"]]["written"]
    STAMPED.append((t, "event", e))
for r in TIMING:
    if "pt1" in r:
        STAMPED.append((r["written"], "timing", r))
    elif r.get("dub"):
        STAMPED.append((air(r["pt0"]) - 1.0 - r.get("left_s", 0), "timing", r))
STAMPED.sort(key=lambda x: x[0])
# each dub clip's length, for the tone that stands in for it
DUB_S = {r["pt0"]: r.get("audio_s") or 2.0 for r in TIMING if r.get("dub") == "ok"}
CLIP_S = {Path(e["dub"]).name: DUB_S.get(e["pt0"], 2.0) for e in EVENTS if e.get("dub")}

S = {"scenario": A.scenario, "t_start": time.time(), "offset": A.offset, "name": RUN["name"], "pending": {},
     "obs": False, "up": {"asr": A.scenario not in ("fresh", "starting"), "mt": A.scenario not in ("fresh", "starting"),
                          "dub": A.scenario != "fresh", "mediamtx": False}}


def vclock() -> float:
    """now on the original run's clock"""
    if S["scenario"] in ("ended", "failed"):
        return RUN.get("ended") or time.time()
    return T0 + DELAY + S["offset"] + (time.time() - S["t_start"])


def shift() -> float:
    """added to wall-clock fields so a replayed live run reads as happening now"""
    return time.time() - vclock() if S["scenario"] == "live" else 0.0


def shifted(r: dict) -> dict:
    d = shift()
    return {k: (v + d if k in ("written", "committed") else v) for k, v in r.items()} if d else r


# ---- the picture: a test pattern over the run's program time, cut like ingest cuts (made once, then cached) ----------
MEDIA = RUN_DIR / "dev_media" / A.media


def make_media():
    if (MEDIA / "done").exists():
        return
    shutil.rmtree(MEDIA, ignore_errors=True)
    MEDIA.mkdir(parents=True)
    dur = END_PT - P0
    font = next((f for f in ("C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") if Path(f).exists()), None)
    text = (f",drawtext=fontfile='{font.replace(':', chr(92) + ':')}':text='PT %{{pts\\:flt\\:{P0:.3f}}}':"
            "fontsize=44:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=10:x=40:y=40") if font else ""
    size = "1280x720" if A.media == "ts" else "854x480"     # VP9 is slow to encode: keep it small
    src = ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30{text}",
           "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000,volume=0.08", "-t", f"{dur:.3f}",
           "-output_ts_offset", f"{P0:.3f}", "-map", "0:v", "-map", "1:a"]
    print(f"making {dur / 60:.0f} min of test picture ({A.media}) in {MEDIA} ...", flush=True)
    if A.media == "ts":
        cmd = ["ffmpeg", "-v", "error", "-y"] + src + [
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
            "-f", "segment", "-segment_time", "1", "-segment_format", "mpegts", "-reset_timestamps", "0",
            "-segment_list_type", "csv", "-segment_list", str(MEDIA / "list.csv"), str(MEDIA / "seg_%06d.ts")]
    else:
        cmd = ["ffmpeg", "-v", "error", "-y"] + src + [
            "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "800k", "-g", "30",
            "-c:a", "libopus", "-b:a", "64k", "-f", "hls", "-hls_time", "1", "-hls_segment_type", "fmp4",
            "-hls_playlist_type", "vod", "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", str(MEDIA / "s%05d.m4s"), str(MEDIA / "raw.m3u8")]
    subprocess.run(cmd, check=True)
    (MEDIA / "done").write_text("ok")


def segments() -> list[tuple[str, float, float]]:
    """(file, start PT, end PT) of the test picture"""
    if A.media == "ts":
        rows = [ln.split(",") for ln in (MEDIA / "list.csv").read_text().splitlines() if ln.count(",") >= 2]
        return [(r[0], float(r[1]), float(r[2])) for r in rows]
    out, t, dur = [], P0, None
    for ln in (MEDIA / "raw.m3u8").read_text().splitlines():
        if ln.startswith("#EXTINF:"):
            dur = float(ln[8:].split(",")[0])
        elif ln and not ln.startswith("#") and dur is not None:
            out.append((ln, t, t + dur)); t += dur; dur = None
    return out


def playlist() -> str:
    """real.playlist()'s format over the test picture, cut at what has 'arrived' by now (live) or complete"""
    if S["scenario"] in ("fresh", "failed", "starting", "blocked"):
        return ""
    edge = P0 + (vclock() - T0) if S["scenario"] == "live" else float("inf")   # PT that has reached us by now
    segs = [s for s in segments() if s[2] <= edge]
    out = ["#EXTM3U", "#EXT-X-VERSION:7",
           f"#EXT-X-TARGETDURATION:{max([1] + [math.ceil(e - s) for _, s, e in segs])}",
           "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:EVENT", "#EXT-X-INDEPENDENT-SEGMENTS"]
    if A.media == "fmp4":
        out.append('#EXT-X-MAP:URI="init.mp4"')
    for f, s, e in segs:
        out += [f"#EXT-X-PROGRAM-DATE-TIME:{real._pdt(s)}", f"#EXTINF:{e - s:.3f},", f]
    if S["scenario"] == "ended":
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"


def tone(seconds: float) -> bytes:
    """a 660 Hz tone with soft edges, 24 kHz mono WAV: stands in for a dub clip of that length"""
    sr = 24000
    n = max(1, int(seconds * sr))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        frames = bytearray()
        for i in range(n):
            env = min(1.0, i / 600, (n - i) / 600)
            frames += struct.pack("<h", int(6000 * env * math.sin(2 * math.pi * 660 * i / sr)))
        w.writeframes(bytes(frames))
    return buf.getvalue()


# ---- status ------------------------------------------------------------------------------------------------
def session():
    s = S["scenario"]
    if s == "fresh":
        return None
    info = {k: v for k, v in RUN.items() if k != "pids"}
    name = S["name"]
    info.update(name=name, src=info.get("src") or "data/devday2026-keynote.mkv", kind=info.get("kind") or "file",
                obs=S["obs"], obs_url=real.OBS_URL if S["obs"] else None, mt_model=info.get("mt_model") or "index-9b",
                media=f"/api/sessions/{name}/media/index.m3u8")
    now = time.time()
    if s == "live":
        d = shift()
        info.update(state="running", created=info["created"] + d, started=info["started"] + d,
                    log=[[t + d, m] for t, m in RUN["log"][:2]])
        info.pop("ended", None)
    elif s == "starting":
        info.update(state="starting", created=now - 20, log=[[now - 19, "启动识别服务（约 30 秒）"]])
        info.pop("started", None); info.pop("ended", None)
    elif s == "failed":
        c = info["created"]
        info.update(state="failed", ended=c + 152, log=[[c + 2, "启动识别服务（约 30 秒）"],
                                                        [c + 152, "启动失败：识别服务无法启动，请查看日志 logs/asr.log"]])
        info.pop("started", None)
    elif s == "blocked":
        info.update(state="running", kind="link", src="https://www.youtube.com/watch?v=QB5BNdBFujE", created=now - 40,
                    started=now - 30, log=[[now - 30, "已开始：点播在首段字幕就绪后播放，直播按设定延时播放"],
                                           [now - 28, BLOCKED]])
        info.pop("ended", None)
    return info


def services():
    out, now = {}, time.time()
    for k, label in (("asr", "识别"), ("mt", "翻译"), ("dub", "配音"), ("mediamtx", "MediaMTX")):
        p = S["pending"].get(k)
        if p and now >= p[1]:
            S["up"][k] = p[0] == "starting"; S["pending"].pop(k); p = None
        if k == "asr" and S["scenario"] == "starting":
            p = ("starting", 0)
        up = S["up"][k]
        v = {"up": up, "label": label}
        if k == "asr" and up:
            cfg = RUN_DIR / "config.json"
            v["boost"] = json.loads(cfg.read_text(encoding="utf-8")).get("asr_boost", []) if cfg.exists() else []
        if k == "mt" and up:
            v.update(model="index-9b", model_path="index-translate-9b-iq4_xs.gguf")
        if k == "dub" and up:
            v.update(model="IndexTTS-2.5", impl="vllm-omni 0.30.0 (patched target_s), steps=25, stage0 no beams",
                     vram_mib=6299, queue=0)
        if p:
            v["pending"] = p[0]
        v["state"] = v.get("pending") or ("up" if up else "down")
        out[k] = v
    return out


BLOCKED = real.RESOLVE_HINTS[0][1]   # what the backend says behind YouTube's bot check


def health():
    s = S["scenario"]
    if s == "ended":
        return {"running": {}, "mem_mb": {}, "anchor": ANCHOR, "playout": {}}
    if s == "blocked":
        return {"running": {"ingest": True, "processor": True}, "mem_mb": {}, "anchor": None, "playout": {},
                "source_error": BLOCKED}
    if s != "live":
        return {}
    return {"running": {"ingest": True, "processor": True}, "mem_mb": {"ingest": 26, "processor": 84},
            "anchor": {**ANCHOR, "T0": T0 + shift()}, "playout": {}}


def status():
    used = (9800 if S["scenario"] == "fresh" else 3075) + (6299 if S["up"]["dub"] else 0) + (4900 if S["up"]["mt"] else 0)
    gpu = {"name": "NVIDIA GeForce RTX 5060 Ti", "used_mb": used, "total_mb": 16311, "util": 37}
    svc = services()
    return {"services": svc, "gpu": gpu, "warnings": real_svc.gpu_warnings(gpu, svc), "session": session(),
            "health": health(), "t": time.time()}


def doctor():
    """fresh: this machine's real check (whatever it lacks); otherwise the same items, all satisfied"""
    items = real_doctor.checks()
    if S["scenario"] != "fresh":
        items = [dict(i, ok=True, fix="") for i in items]
    return {"items": items, **real_doctor.summary(items)}


PROVIDERS = {"deepseek": {"name": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "",
                          "has_key": False, "key_url": "https://platform.deepseek.com/api_keys", "problem": "未填写 API key"},
             "custom": {"name": "custom", "label": "自定义接口（OpenAI 兼容）", "base_url": "", "model": "",
                        "has_key": False, "key_url": "", "problem": "未填写 API key"}}


YOUTUBE = {"cookies": "", "cookies_from_browser": "", "cookies_found": False}   # kept here: settings.json stays as is


def log_tail(name):
    lines = [f"[{time.strftime('%H:%M:%S')}] (mock) {name} log line {i}" for i in range(8)]
    return {f: "\n".join(lines) for f in real_svc.info()[name]["logs"]}


def sessions():
    out = []
    for rj in sorted(real.RUNS.glob("*/run.json"), reverse=True)[:50]:
        i = json.loads(rj.read_text(encoding="utf-8"))
        out.append({k: i.get(k) for k in ("name", "title", "state", "created", "started", "ended", "delay_s", "dub")})
    return out


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code=200):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in (("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Headers", "Content-Type"),
                     ("Access-Control-Allow-Methods", "GET, POST"), ("Content-Length", "0")):
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/status":
            return self._json(status())
        if p == "/api/configs":
            return self._json(real.list_configs())
        if p == "/api/models":     # everything counts as ready here: nothing runs
            return self._json([dict(m, ready=True, note="") for m in real.list_models()])
        if p == "/api/sessions":
            return self._json(sessions())
        if p == "/api/services":
            return self._json(real_svc.info())
        m = re.fullmatch(r"/api/services/(\w+)/log", p)
        if m and m[1] in S["up"]:
            return self._json(log_tail(m[1]))
        if p == "/api/doctor":
            return self._json(doctor())
        if p == "/api/settings/online":
            return self._json(list(PROVIDERS.values()))
        if p == "/api/settings/youtube":
            return self._json(YOUTUBE)
        m = re.fullmatch(r"/api/sessions/([\w.-]+)/media/(index\.m3u8|[\w.-]+)", p)
        if m:
            if m[2] == "index.m3u8":
                text = playlist()
                return self._send(text.encode(), "application/vnd.apple.mpegurl") if text else self._json({}, 404)
            f = MEDIA / m[2]
            ctype = {".ts": "video/mp2t", ".m4s": "video/iso.segment", ".mp4": "video/mp4"}.get(f.suffix, "")
            return self._send(f.read_bytes(), ctype) if f.is_file() and ctype else self._json({}, 404)
        m = re.fullmatch(r"/api/sessions/([\w.-]+)/clips/(dub_\d{5}\.wav)", p)
        if m:
            f = RUN_DIR / "clips" / m[2]
            return self._send(f.read_bytes() if f.is_file() else tone(CLIP_S.get(m[2], 2.0)), "audio/wav")
        if p.startswith("/api/sessions/"):
            d = real.run_detail(p.rsplit("/", 1)[1])
            return self._json(d) if d else self._json({"detail": "找不到这一场"}, 404)
        if p == "/api/stream":
            return self.stream()
        self._json({"detail": "not found"}, 404)

    def do_POST(self):
        p = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        parts = p.strip("/").split("/")
        if p == "/api/session/start":
            S.update(scenario="starting", t_start=time.time(), obs=bool(body.get("obs")),
                     name=time.strftime("%Y%m%d-%H%M%S") + "-" + (body.get("preset") or "default"))
            threading.Timer(6, lambda: S.update(scenario="live", t_start=time.time(), offset=-DELAY + 3)).start()
            S["up"]["asr"] = S["up"]["mt"] = True
            return self._json(session())
        if p == "/api/session/stop":
            S.update(scenario="ended")
            return self._json(session())
        if p == "/api/settings/online":
            v = PROVIDERS.get(body.get("provider"))
            if not v:
                return self._json({"detail": "没有这个接口"}, 404)
            v.update({k: body[k] for k in ("base_url", "model") if body.get(k) is not None})
            v["has_key"] = v["has_key"] or bool(body.get("api_key"))
            v["problem"] = None if v["has_key"] and v["base_url"] else "未填写 API key"
            return self._json(list(PROVIDERS.values()))
        if p == "/api/settings/youtube":
            f = (body.get("cookies") or "").strip().strip('"')
            if f and not Path(f).is_file():
                return self._json({"detail": f"找不到 cookies 文件：{f}"}, 400)
            YOUTUBE.update(cookies=f, cookies_from_browser=body.get("cookies_from_browser") or "", cookies_found=bool(f))
            return self._json(YOUTUBE)
        m = re.fullmatch(r"/api/settings/online/(\w+)/test", p)
        if m:
            return self._json({"ok": True, "reply": "你好", "wall_s": 0.42})
        if len(parts) == 4 and parts[1] == "services" and parts[2] in S["up"]:
            S["pending"][parts[2]] = ("stopping" if parts[3] == "stop" else "starting", time.time() + 4)
            return self._json({"ok": True})
        self._json({"detail": "not found"}, 404)

    def stream(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(kind, data):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        try:
            self.wfile.write(b"retry: 2000\n\n")
            cur, sent, last = object(), 0, 0.0
            while True:
                s = session()
                name = s["name"] if s else None
                if time.time() - last >= 2:
                    last = time.time()
                    send("status", status())
                if name != cur:
                    cur, sent = name, 0
                    send("reset", {"name": name})
                if S["scenario"] in ("live", "ended"):
                    v = vclock() if S["scenario"] == "live" else float("inf")
                    batch = {"event": [], "timing": []}
                    while sent < len(STAMPED) and STAMPED[sent][0] <= v:
                        batch[STAMPED[sent][1]].append(shifted(STAMPED[sent][2])); sent += 1
                    for k in ("event", "timing"):
                        if batch[k]:
                            send(k, batch[k])
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return


if __name__ == "__main__":
    make_media()
    srv = ThreadingHTTPServer(("127.0.0.1", A.port), H)
    print(f"mock livedub.server on http://127.0.0.1:{A.port}: {A.scenario}, replaying {RUN_DIR.name} "
          f"({len(STAMPED)} records, picture {A.media})", flush=True)
    srv.serve_forever()
