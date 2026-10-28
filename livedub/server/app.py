"""HTTP API for the desktop app (127.0.0.1 only): service status, start / stop and logs, the environment check,
downloads and the download folder, online translation settings, session start / stop, past sessions (the list, one session's records), a session's
picture for the app's player (HLS playlist + chunks) and its dub clips, and a Server-Sent Events stream with status
every 2 s plus the session's subtitle and timing records as they are written. The built UI (app/dist) is served at /,
so a browser can open it too."""
import asyncio
import json
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__, doctor, install, mt_chat, settings
from ..settings import ROOT
from . import session as sess
from .services import LABELS, Services, gpu_warnings, info as service_info, tail

services = Services()
session: sess.Session | None = None
health: dict = {}


async def poll():
    global health
    while True:
        try:
            await services.refresh()
            health = await asyncio.to_thread(session.health)
        except Exception as e:
            print("poll:", e, flush=True)
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app):
    global session
    session = sess.Session(services)
    task = asyncio.create_task(poll())
    yield
    task.cancel()


app = FastAPI(title="live-dub", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://tauri.localhost", "https://tauri.localhost",
                                                  "tauri://localhost", "http://localhost:1420",
                                                  "http://127.0.0.1:8090"],
                   allow_methods=["*"], allow_headers=["*"])


def snapshot() -> dict:
    assert session is not None
    # t is the moment of sending (services.status carries the time of its last poll, seconds older under load)
    # health is polled every few seconds: right after a new start it still describes the previous run, whose
    # anchor would send the player to that run's schedule (the end of a file that was just opened)
    current = health if health.get("name") == (session.info or {}).get("name") else {}
    st = {**services.status, "session": session.info, "health": current, "t": time.time(),
          "version": __version__, "downloads": install.jobs(), "demo": DEMO}
    if session.active and services.status:   # during a session only what it uses matters (dubbing joins at its start)
        st["warnings"] = gpu_warnings(services.status.get("gpu", {}), services.status.get("services", {}),
                                      session.info.get("mt_model"), want_dub=False)
    return st


@app.get("/api/status")
async def status():
    if not services.status:
        await services.refresh()
    return snapshot()


@app.get("/api/configs")
async def configs():
    return sess.list_configs()


DEMO = str(settings.RESOURCES / "demo.mp4") if (settings.RESOURCES / "demo.mp4").exists() else None   # a test video


@app.get("/api/downloads")
async def downloads():
    """What this machine can download: label, approximate size in bytes, sources. Progress is in the status."""
    return install.catalog()


class DownloadReq(BaseModel):
    source: str = ""     # "" = the first source that answers


@app.post("/api/downloads/{key}")
async def download_start(key: str, req: DownloadReq):
    """Queue a download (or resume a paused one); for one already running, switch it to `source`."""
    try:
        return install.start(key, req.source)
    except (RuntimeError, ValueError) as e:
        raise HTTPException(409, str(e))


@app.post("/api/downloads/{key}/pause")
async def download_pause(key: str):
    return install.pause(key)


def _download_dir() -> dict:
    d = install.model_dir()
    near = next(p for p in (d, *d.parents) if p.exists())
    return {"dir": str(d), "free": shutil.disk_usage(near).free,
            "used": sum(p.stat().st_size for p in install.downloaded())}


@app.get("/api/settings/downloads")
async def download_dir():
    return _download_dir()


class DirReq(BaseModel):
    dir: str


@app.post("/api/settings/downloads")
async def download_dir_set(req: DirReq):
    """A new download folder: the models downloaded so far move there (after the downloads under way)."""
    assert session is not None
    dest = Path(os.path.expanduser(req.dir.strip().strip('"')))
    if not dest.is_absolute():
        raise HTTPException(400, "请填写完整的文件夹路径，例如 D:\\LiveDub\\models")
    if dest.exists() and dest.resolve() == install.model_dir().resolve():
        return _download_dir()
    if session.active:
        raise HTTPException(409, "请先停止观看，再更改下载位置")
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"无法使用这个文件夹：{e}")
    need = _download_dir()["used"]
    if os.stat(dest).st_dev != os.stat(next(p for p in (install.model_dir(), *install.model_dir().parents)
                                             if p.exists())).st_dev and need > shutil.disk_usage(dest).free:
        raise HTTPException(400, f"新位置所在磁盘空间不足：已下载的模型共 {need / 1e9:.1f} GB")
    for name in ("asr", "mt"):   # they keep the model files open
        if services.status.get("services", {}).get(name, {}).get("up"):
            await services.stop(name)
            if not await services.wait_down(name, 30):
                raise HTTPException(409, f"{LABELS.get(name, name)}未停止，请稍后重试")
    try:
        install.move(dest)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {**_download_dir(), "dir": str(dest)}


@app.get("/api/models")
async def models():
    return await asyncio.to_thread(sess.list_models)


class SvcReq(BaseModel):
    config: str | None = None
    model: str | None = None


@app.get("/api/services")
async def services_info():
    return service_info()


@app.get("/api/services/{name}/log")
async def service_log(name: str, lines: int = 200):
    if name not in LABELS:
        raise HTTPException(404)
    return await asyncio.to_thread(tail, name, max(10, min(lines, 2000)))


@app.get("/api/doctor")
async def doctor_report():
    items = await asyncio.to_thread(doctor.checks)
    return {"items": items, **doctor.summary(items)}


def _providers() -> list[dict]:
    out = []
    for name, base in mt_chat.PROVIDERS.items():
        p = mt_chat.provider(name)
        out.append({"name": name, "label": base.get("label", name), "base_url": p.get("base_url", ""),
                    "model": p.get("model", ""), "has_key": bool(p.get("api_key")), "key_url": base.get("key_url", ""),
                    "problem": mt_chat.problem(name, "" if name == "custom" else "x")})
    return out


class OnlineReq(BaseModel):
    provider: str
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None      # empty: keep the saved one


@app.get("/api/settings/online")
async def online_get():
    return _providers()


@app.post("/api/settings/online")
async def online_set(req: OnlineReq):
    """Save a provider's key (and base_url / model) to .secret/<provider>.json; the key is never sent back."""
    if req.provider not in mt_chat.PROVIDERS:
        raise HTTPException(404, f"找不到线上翻译接口：{req.provider}")
    f = settings.secret_file(req.provider)
    cur = {}
    if f.exists():
        try:
            cur = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            cur = {}
    for k in ("base_url", "model", "api_key"):
        v = getattr(req, k)
        if v is not None and (v.strip() or k != "api_key"):
            cur[k] = v.strip()
    f.parent.mkdir(exist_ok=True)
    f.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        os.chmod(f, 0o600)   # only this user (no effect on Windows, where the profile's ACLs apply)
    except OSError:
        pass
    return _providers()


# ---- YouTube sign-in ----------------------------------------------------------------------------------------
BROWSERS = ("firefox", "edge", "chrome", "brave", "chromium", "opera", "vivaldi", "safari", "whale")   # as yt-dlp names them


class YoutubeReq(BaseModel):
    cookies: str = ""                # a cookies.txt exported from a browser (Netscape format)
    cookies_from_browser: str = ""   # or BROWSER[+KEYRING][:PROFILE] as yt-dlp takes it; the file wins when both are set


def _youtube() -> dict:
    yt = settings.get()["youtube"]
    f = yt.get("cookies") or ""
    return {"cookies": f, "cookies_from_browser": yt.get("cookies_from_browser") or "",
            "cookies_found": bool(f) and settings.path(f).is_file()}


@app.get("/api/settings/youtube")
async def youtube_get():
    return _youtube()


@app.post("/api/settings/youtube")
async def youtube_set(req: YoutubeReq):
    """Where yt-dlp gets the sign-in for sites that ask (YouTube's bot check), saved to settings.json and used from the
    next session on. Only the file's path or the browser's name is stored; the cookies stay where they are."""
    f, b = req.cookies.strip().strip('"'), req.cookies_from_browser.strip()
    if f and not settings.path(f).is_file():
        raise HTTPException(400, f"找不到 cookies 文件：{f}")
    if b and b.replace("+", ":").split(":")[0].strip().lower() not in BROWSERS:
        raise HTTPException(400, f"不支持从这个浏览器读取 Cookie：{b}")
    settings.save({"youtube": {"cookies": f, "cookies_from_browser": b}})
    return _youtube()


@app.post("/api/settings/online/{name}/test")
async def online_test(name: str):
    """One tiny request with the saved settings: says whether the key, address and model work."""
    if name not in mt_chat.PROVIDERS:
        raise HTTPException(404)
    p = mt_chat.provider(name)
    model = p.get("model") or next((m.get("model") for m in sess.mt.models().values() if m.get("api") == name), "")
    why = mt_chat.problem(name, model)
    if why:
        raise HTTPException(409, why)

    def ping():
        import urllib.error
        import urllib.request
        body = {"model": model, "messages": [{"role": "user", "content": "Translate to Chinese: hello"}],
                "max_tokens": 16}
        if p.get("deepseek_thinking"):
            body["thinking"] = {"type": "disabled"}
        req = urllib.request.Request(p["base_url"].rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + p["api_key"]})
        t = time.time()
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.load(r)
            return {"ok": True, "reply": j["choices"][0]["message"]["content"][:60], "wall_s": round(time.time() - t, 2)}
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"[:240]}
    return await asyncio.to_thread(ping)


@app.post("/api/services/{name}/{action}")
async def service(name: str, action: str, req: SvcReq | None = None):
    if name not in LABELS or action not in ("start", "stop", "restart"):
        raise HTTPException(404)
    assert session is not None
    if session.active and action != "start":
        raise HTTPException(409, "观看进行中不能停止服务，请先停止观看")
    req = req or SvcReq()
    kw = {}
    if req.config:
        # checked before a restart stops anything; an unknown name used to start the default preset without a word
        kw["config"] = sess.preset_file(req.config)
        if not kw["config"]:
            raise HTTPException(404, f"找不到场景预设：{req.config}")
    if req.model:
        kw["model"] = req.model
    if action in ("stop", "restart"):
        await services.stop(name)
        await services.wait_down(name, 60)
    if action in ("start", "restart"):
        try:
            await services.start(name, **kw)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
    return {"ok": True}


class StartReq(BaseModel):
    preset: str = "default"
    src: str | None = None          # a URL or a local video path; default: the preset's own
    delay: float | None = None
    dub: bool = True
    obs: bool = False               # also burn and push a stream for OBS (MediaMTX)
    mt_model: str | None = None


@app.post("/api/session/start")
async def start(req: StartReq):
    if req.delay is not None and not 10 <= req.delay <= 120:
        raise HTTPException(400, "延时要在 10 到 120 秒之间")
    try:
        return await session.start(req.preset, req.src, req.delay, req.dub, req.obs, req.mt_model)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/session/stop")
async def stop():
    try:
        return await session.stop()
    except RuntimeError as e:
        raise HTTPException(409, str(e))


class PriorityReq(BaseModel):
    pt: float


@app.post("/api/session/priority")
async def prioritize(req: PriorityReq):
    assert session is not None
    import math
    if not math.isfinite(req.pt):
        raise HTTPException(400, "播放位置无效")
    try:
        await asyncio.to_thread(session.prioritize, req.pt)
    except (RuntimeError, OSError) as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.get("/api/sessions")
async def sessions():
    out = []
    for rj in sorted(sess.RUNS.glob("*/run.json"), reverse=True)[:50] if sess.RUNS.exists() else []:
        try:
            i = json.loads(rj.read_text(encoding="utf-8"))
            out.append({k: i.get(k) for k in ("name", "title", "state", "created", "started", "ended", "delay_s", "dub")})
        except Exception:
            pass
    return out


@app.get("/api/sessions/{name}")
async def session_detail(name: str):
    d = await asyncio.to_thread(sess.run_detail, name)
    if d is None:
        raise HTTPException(404, f"找不到这一场：{name}")
    return d


@app.get("/api/sessions/{name}/media/index.m3u8")
async def media_playlist(name: str):
    text = await asyncio.to_thread(sess.playlist, name)
    if text is None:
        raise HTTPException(404, "这一场没有可播放的画面")
    return Response(text, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})


@app.get("/api/sessions/{name}/subtitles.srt")
async def export_subtitles(name: str, language: str = "zh"):
    if language not in ("zh", "en", "both"):
        raise HTTPException(400, "字幕语言无效")
    try:
        text = await asyncio.to_thread(sess.subtitles, name, language)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return Response(text.encode("utf-8-sig"), media_type="application/x-subrip",
                    headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'})


@app.delete("/api/sessions/{name}")
async def delete_session(name: str, cache_only: bool = False):
    assert session is not None
    if session.active and (session.info or {}).get("name") == name:
        raise HTTPException(409, "请先停止本场，再清理")
    try:
        await asyncio.to_thread(sess.remove_run, name, cache_only)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e))
    if (session.info or {}).get("name") == name:
        if cache_only:
            assert session.info is not None
            session.info["media"] = None
            session._save()
        else:
            session.info = None
            session.dir = None
    return {"ok": True}


@app.get("/api/sessions/{name}/media/{seg}")
async def media_segment(name: str, seg: str):
    p = sess.media_file(name, "seg", seg)
    if p is None:
        raise HTTPException(404)
    return FileResponse(p, media_type="video/mp2t", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/sessions/{name}/clips/{clip}")
async def media_clip(name: str, clip: str):
    p = sess.media_file(name, "clip", clip)
    if p is None:
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})


def _read_new(path: Path, offs: dict) -> list[dict]:
    try:
        size = path.stat().st_size
    except OSError:
        return []
    off = offs.get(path, 0)
    if size <= off:
        return []
    with open(path, "rb") as f:
        f.seek(off)
        data = f.read(size - off)
    end = data.rfind(b"\n") + 1          # only whole lines; a half-written one is picked up next time
    offs[path] = off + end
    rows = []
    for line in data[:end].decode("utf-8", "replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def _sse(kind: str, data) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/stream")
async def stream(request: Request):
    async def gen():
        cur, offs, last = None, {}, 0.0
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            if time.time() - last >= 2:
                last = time.time()
                yield _sse("status", snapshot())
            if session.dir != cur:
                cur, offs = session.dir, {}
                yield _sse("reset", {"name": (session.info or {}).get("name")})
            if cur:
                for fname, kind in (("events.jsonl", "event"), ("timing.jsonl", "timing")):
                    rows = await asyncio.to_thread(_read_new, cur / fname, offs)
                    if rows:
                        yield _sse(kind, rows)
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


dist = settings.RESOURCES / "app" / "dist"
if dist.exists():   # the built UI, so it can also be opened in a browser for debugging
    app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
