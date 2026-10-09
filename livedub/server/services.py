"""The resident services a session needs, all on this machine, ports from settings:
  asr       Parakeet ASR + TitaNet speaker embeddings (svc/asr_service.py), CPU, on the Linux side (WSL on Windows)
  mt        llama-server with a local translation model; not needed when the session translates online
  dub       dubbing (dubsvc/server.py), Linux side, GPU; with the default backend it fronts a vLLM-Omni server
  mediamtx  a local RTMP server, only for the optional OBS output
Status is polled; start / stop build the commands from settings (livedub/settings.py). Logs go to logs/<service>.log.

CLI (the same as the app's service buttons):  python -m livedub.server.services status | start NAME | stop NAME
"""
import asyncio
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .. import mt, settings
from ..settings import ROOT, sh, to_linux
from . import procs

LABELS = {"asr": "识别", "mt": "翻译", "dub": "配音", "mediamtx": "MediaMTX"}
START_TIMEOUT = {"asr": 150, "mt": 180, "dub": 600, "mediamtx": 30}
TMP = ROOT / ".tmp"
OS_NAME = {"Windows": "Windows", "Linux": "Linux", "Darwin": "macOS"}.get(platform.system(), platform.system())
OMNI_ENV = {   # vLLM inside WSL: no UVA for the V2 model runner, no nvcc for the FlashInfer sampler
    "VLLM_USE_V2_MODEL_RUNNER": "0", "VLLM_USE_FLASHINFER_SAMPLER": "0", "FLASHINFER_DISABLE_VERSION_CHECK": "1",
    "PYTHONUNBUFFERED": "1", "LIVEDUB_OMNI_FACTOR_LOG": "/tmp/livedub_omni_factor.jsonl"}


def _get(url: str, timeout: float = 2.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "null")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def log_path(name: str) -> Path:
    return settings.logs_dir() / f"{name}.log"


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def model_key_of(path: str) -> str | None:
    """The translation model key whose GGUF a running llama-server loaded."""
    name = Path(path or "").name
    return next((k for k in mt.models() if mt.gguf_path(k) and Path(mt.gguf_path(k)).name == name), None)


def _exports(env: dict) -> str:
    return " ".join(f"{k}={sh(str(v))}" for k, v in env.items())


def info() -> dict:
    """What each service is and where it runs, for the services page."""
    s = settings.get()
    lin = "WSL" if settings.linux_runtime() == "wsl" else OS_NAME
    dub = s["dub"]
    dub_what = {"omni": "IndexTTS-2.5（vLLM-Omni），克隆说话人音色", "local": "IndexTTS-2.5（进程内），克隆说话人音色",
                "voxcpm": "VoxCPM2，克隆说话人音色"}.get(dub["backend"], dub["backend"])
    return {
        "asr": {"what": "Parakeet 英文识别 + TitaNet 说话人", "where": f"{lin}，CPU", "port": str(settings.port("asr")),
                "boot": "约 30 秒", "logs": [rel(log_path("asr"))]},
        "mt": {"what": "本地翻译模型（llama-server）；用线上翻译时不需要", "where": f"{OS_NAME}，GPU",
               "port": str(settings.port("mt")), "boot": "约 1 分钟", "logs": [rel(log_path("mt"))]},
        "dub": {"what": dub_what, "where": f"{lin}，GPU（约 {dub['vram_gb']:g} GB）",
                "port": (f"{settings.port('dub')} → {settings.port('omni')}" if dub["backend"] == "omni"
                         else str(settings.port("dub"))),
                "boot": "约 1.5–3 分钟", "logs": [rel(log_path("dub"))] + ([rel(log_path("omni"))] if dub["backend"] == "omni" else [])},
        "mediamtx": {"what": "本机 RTMP 服务：只在「输出到 OBS」时用，OBS 从这里拉流", "where": OS_NAME,
                     "port": str(settings.port("rtmp")), "boot": "几秒", "logs": [rel(log_path("mediamtx"))]},
    }


def tail(name: str, lines: int = 200) -> dict:
    """The last lines of a service's log(s)."""
    out = {}
    for f in info()[name]["logs"]:
        p = ROOT / f if not Path(f).is_absolute() else Path(f)
        try:
            with open(p, "rb") as fh:
                fh.seek(max(0, p.stat().st_size - 256 * 1024))
                out[f] = "\n".join(fh.read().decode("utf-8", "replace").splitlines()[-lines:])
        except OSError:
            out[f] = ""
    return out


def gpu() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             creationflags=settings.NO_WINDOW).stdout
        name, used, total, util = [x.strip() for x in out.strip().splitlines()[0].split(",")]
        return {"name": name, "used_mb": int(used), "total_mb": int(total), "util": int(util)}
    except Exception:
        return {}


def gpu_warnings(g: dict, svc: dict, mt_key: str | None = None, want_dub: bool = True) -> list[str]:
    """Plain warnings about the graphics card: no NVIDIA card, or too little free memory for what would still have to
    start (the local translation model mt_key, the dubbing service) and is not running yet."""
    if not g:
        return ["没检测到 NVIDIA 显卡（nvidia-smi）：本地翻译和配音要用 N 卡；可以用线上翻译，只出字幕。"]
    free = (g["total_mb"] - g["used_mb"]) / 1024
    need = []
    m = mt.models().get(mt_key or settings.get()["mt"]["default"], {})
    if not svc.get("mt", {}).get("up") and m.get("vram_gb") and not m.get("api"):
        need.append(("本地翻译", m["vram_gb"]))
    if want_dub and not svc.get("dub", {}).get("up"):
        need.append(("配音", settings.get()["dub"]["vram_gb"]))
    short = [f"{n}约 {v:g} GB" for n, v in need if v > free]
    if short:
        return [f"显存只剩约 {free:.1f} GB，" + "、".join(short) + "，可能不够：关掉别的占显存的程序，或者改用线上翻译、不开配音。"]
    return []


def linux_missing(paths: dict[str, str]) -> list[str]:
    """Labels of the Linux-side paths that do not exist; RuntimeError when the Linux side itself does not answer."""
    tests = " ; ".join(f"[ -e {sh(p)} ] || echo {sh(label)}" for label, p in paths.items())
    side = "WSL" if settings.linux_runtime() == "wsl" else "bash"
    try:
        r = settings.linux_run(tests + " ; echo __ok__", timeout=90)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"调不起 Linux 环境（{side}）：{e}")
    lines = [l.strip() for l in r.stdout.replace("\x00", "").splitlines() if l.strip()]
    if "__ok__" not in lines:   # wsl.exe's own errors come back as UTF-16: drop the NULs and show the gist
        msg = ((r.stdout or "") + (r.stderr or "")).replace("\x00", "").strip()
        raise RuntimeError(f"Linux 环境（{side}）没响应：{msg[-200:] or r.returncode}")
    return [l for l in lines if l != "__ok__"]


class Services:
    def __init__(self):
        self.status: dict = {}
        self.pending: dict[str, tuple[str, float]] = {}   # name -> (starting|stopping, since)
        self.errors: dict[str, str] = {}                  # name -> why the last start failed (shown until next start)

    # ---- status -------------------------------------------------------------------------------------------
    def _probe(self) -> dict:
        s = {}
        try:
            h = _get(settings.url("asr") + "/health")
            s["asr"] = {"up": bool(h.get("ok")), "boost": h.get("boost", [])}
        except Exception:
            s["asr"] = {"up": False}
        try:
            _get(settings.url("mt") + "/health")
            path = (_get(settings.url("mt") + "/props") or {}).get("model_path", "")
            s["mt"] = {"up": True, "model": model_key_of(path), "model_path": Path(path).name}
        except Exception:
            s["mt"] = {"up": False}
        try:
            h = _get(settings.url("dub") + "/health")
            s["dub"] = {"up": bool(h.get("ok")), "model": h.get("model"), "impl": h.get("impl"),
                        "vram_mib": h.get("vram_mib"), "queue": h.get("queue")}
        except Exception:
            s["dub"] = {"up": False}
        s["mediamtx"] = {"up": _port_open(settings.port("rtmp"))}
        now = time.time()
        for name, st in s.items():
            st["label"] = LABELS[name]
            p = self.pending.get(name)
            if p:
                kind, since = p
                done = st["up"] if kind == "starting" else not st["up"]
                if done or now - since > START_TIMEOUT[name]:
                    self.pending.pop(name, None)
                    if not done:
                        self.errors[name] = ("启动超时，看日志 " + " ".join(info()[name]["logs"]) if kind == "starting"
                                             else "停止超时")
                else:
                    st["pending"] = kind
            if st["up"]:
                self.errors.pop(name, None)
            if self.errors.get(name):
                st["error"] = self.errors[name]
            st["state"] = st.get("pending") or ("up" if st["up"] else "down")
        return s

    async def refresh(self) -> dict:
        s = await asyncio.to_thread(self._probe)
        g = await asyncio.to_thread(gpu)
        self.status = {"services": s, "gpu": g, "warnings": gpu_warnings(g, s), "t": time.time()}
        return self.status

    # ---- start / stop -------------------------------------------------------------------------------------
    @staticmethod
    def _linux_check(paths: dict[str, str], what: str):
        """Fail early, in words, when the Linux side or a file the service needs is missing."""
        missing = linux_missing(paths)
        if missing:
            raise RuntimeError(f"{what}还没装好，缺：{'、'.join(missing)}（见 docs/安装.md）")

    def start_asr(self, config: Path):
        a = settings.get()["asr"]
        self._linux_check({"识别的 Python 环境": a["python"], "Parakeet 模型": a["model"],
                           "TitaNet 模型": a["speaker_model"]}, "识别服务")
        cmd = (f"exec env CUDA_VISIBLE_DEVICES= PYTHONUNBUFFERED=1 {sh(a['python'])} "
               f"{sh(to_linux(ROOT / 'svc' / 'asr_service.py'))} --config {sh(to_linux(config))} "
               f"--port {settings.port('asr')} --device {sh(a['device'])} --threads {int(a['threads'])} "
               f"--model {sh(a['model'])} --speaker-model {sh(a['speaker_model'])}")
        procs.spawn(settings.linux_argv(cmd), log_path("asr"), cwd=ROOT)

    def stop_asr(self):
        settings.linux_run("pkill -f '[a]sr_service.py'; true", timeout=30)
        procs.kill(procs.find(r"asr_service\.py", ("wsl", "bash")))

    def start_mt(self, model_key: str):
        exe = settings.tool("llama_server")
        if not exe:
            raise RuntimeError("找不到 llama-server：运行 python scripts/fetch.py llama 下载，或在 settings.json 的 "
                               "tools.llama_server 填它的路径")
        gguf = mt.gguf_path(model_key)
        if not Path(gguf).is_file():
            raise RuntimeError(f"翻译模型文件不在：{gguf}（python scripts/fetch.py model {model_key} 下载）")
        env = {}
        tools = ROOT / "tools" / "llama.cpp"
        if not settings.WINDOWS and Path(exe).is_relative_to(tools):
            # a release archive from scripts/fetch.py: the CUDA runtime libraries sit next to or below the binary
            top = tools / Path(exe).relative_to(tools).parts[0]
            libs = sorted({str(f.parent) for f in top.rglob("*.so*")})
            env["LD_LIBRARY_PATH"] = ":".join(libs + [os.environ.get("LD_LIBRARY_PATH", "")]).strip(":")
        procs.spawn(mt.llama_argv(model_key), log_path("mt"), cwd=Path(exe).parent, env=env)

    def stop_mt(self):
        procs.kill(procs.find(rf"--port\s+{settings.port('mt')}\b", ("llama-server",)))

    def start_dub(self):
        d = settings.get()["dub"]
        need = {"配音的 Python 环境": d["python"]}
        if d["backend"] == "omni":
            need.update({"vLLM-Omni": d["vllm"], "IndexTTS-2.5 模型": d["model"]})
        elif d["backend"] == "local":
            need.update({"index-tts 代码": d["indextts_repo"], "IndexTTS-2.5 模型": d["model"]})
        elif d["backend"] == "voxcpm":
            need.update({"VoxCPM2 模型": d["voxcpm_model"]})
        self._linux_check(need, "配音服务")
        env = {**({"PYTHONUNBUFFERED": "1"}), **d.get("env", {})}
        if d["backend"] == "omni" and not _port_open(settings.port("omni")):
            oenv = {**OMNI_ENV, **d.get("env", {})}
            cmd = (f"cd {sh(d['omni_dir'])} 2>/dev/null; export {_exports(oenv)}; : > \"$LIVEDUB_OMNI_FACTOR_LOG\"; "
                   # GPU memory in use before it starts, so dubsvc can tell roughly what it holds
                   "(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 > /tmp/livedub_omni_base_mib) "
                   "2>/dev/null; "
                   f"exec {sh(d['vllm'])} serve {sh(d['model'])} --omni --trust-remote-code --host 127.0.0.1 "
                   f"--port {settings.port('omni')} --deploy-config {sh(to_linux(settings.path(d['deploy_config'])))}")
            procs.spawn(settings.linux_argv(cmd), log_path("omni"), cwd=ROOT)
        args = ["--backend", d["backend"], "--port", str(settings.port("dub")),
                "--omni-url", settings.url("omni"), "--model", d["model"], "--indextts-repo", d["indextts_repo"],
                "--voxcpm-model", d["voxcpm_model"]]
        if d.get("warmup_ref"):
            args += ["--warmup-ref", d["warmup_ref"]]
        args += [str(x) for x in d.get("args", [])]
        cmd = (f"export {_exports(env)}; exec {sh(d['python'])} {sh(to_linux(ROOT / 'dubsvc' / 'server.py'))} "
               + " ".join(sh(x) for x in args))
        procs.spawn(settings.linux_argv(cmd), log_path("dub"), cwd=ROOT)

    def stop_dub(self):
        omni = settings.port("omni")
        settings.linux_run(f"pkill -f '[d]ubsvc/server.py'; pkill -f '[v]llm serve .*--port {omni}'; sleep 5; "
                           # vLLM-Omni's engine-core children can outlive their parent and keep the GPU memory
                           "pkill -f '[S]tageEngineCoreProc'; true", timeout=60)
        procs.kill(procs.find(r"dubsvc[/\\]server\.py|vllm serve", ("wsl", "bash")))

    def start_mediamtx(self):
        exe = settings.tool("mediamtx")
        if not exe:
            raise RuntimeError("找不到 MediaMTX：运行 python scripts/fetch.py mediamtx 下载，或在 settings.json 的 "
                               "tools.mediamtx 填它的路径")
        TMP.mkdir(exist_ok=True)
        cfg = TMP / "mediamtx.yml"
        # RTMP on this machine only (the playout pushes, OBS pulls); every other protocol off
        cfg.write_text(f"logLevel: warn\nrtsp: no\nrtmp: yes\nrtmpAddress: 127.0.0.1:{settings.port('rtmp')}\n"
                       "hls: no\nwebrtc: no\nsrt: no\napi: no\nmetrics: no\npprof: no\nplayback: no\n"
                       "paths:\n  all_others:\n", encoding="utf-8")
        procs.spawn([exe, str(cfg)], log_path("mediamtx"), cwd=Path(exe).parent)

    def stop_mediamtx(self):
        procs.kill(procs.find(re.escape(str(TMP / "mediamtx.yml")), ("mediamtx",)))   # ours only

    async def start(self, name: str, **kw):
        up = (self.status.get("services", {}).get(name) or {}).get("up")
        if up or name in self.pending:
            return
        self.errors.pop(name, None)
        self.pending[name] = ("starting", time.time())
        try:
            if name == "asr":
                await asyncio.to_thread(self.start_asr, kw.get("config") or ROOT / "configs" / "default.json")
            elif name == "mt":
                key = kw.get("model") or settings.get()["mt"]["default"]
                if mt.models().get(key, {}).get("api"):   # online model: nothing local to start
                    self.pending.pop(name, None)
                    return
                await asyncio.to_thread(self.start_mt, key)
            elif name == "dub":
                await asyncio.to_thread(self.start_dub)
            elif name == "mediamtx":
                await asyncio.to_thread(self.start_mediamtx)
        except Exception as e:
            self.pending.pop(name, None)
            self.errors[name] = str(e)
            raise

    async def stop(self, name: str):
        self.pending[name] = ("stopping", time.time())
        try:
            await asyncio.to_thread(getattr(self, f"stop_{name}"))
        except Exception:
            self.pending.pop(name, None)
            raise

    async def wait_up(self, name: str, timeout: float, check=None) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            s = (await self.refresh())["services"][name]
            if s["up"] and (check is None or check(s)):
                return True
            if name not in self.pending and not s["up"]:
                return False   # the start failed or timed out
            await asyncio.sleep(2)
        return False

    async def wait_down(self, name: str, timeout: float = 30) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if not (await self.refresh())["services"][name]["up"]:
                return True
            await asyncio.sleep(1)
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description="status / start / stop the services (as the app's buttons do)")
    ap.add_argument("action", choices=["status", "start", "stop"])
    ap.add_argument("name", nargs="?", choices=list(LABELS))
    ap.add_argument("--config", help="preset for the ASR hotwords (default configs/default.json)")
    ap.add_argument("--model", help="translation model key (default settings mt.default)")
    ap.add_argument("--wait", action="store_true", help="start: wait until it answers")
    a = ap.parse_args()
    svc = Services()

    async def run():
        if a.action == "status" or not a.name:
            st = await svc.refresh()
            print(json.dumps(st, ensure_ascii=False, indent=1))
            return
        if a.action == "stop":
            await svc.stop(a.name)
            print("stopped" if await svc.wait_down(a.name, 60) else "still up")
            return
        await svc.refresh()
        kw = {"config": settings.path(a.config)} if a.config else {}
        if a.model:
            kw["model"] = a.model
        await svc.start(a.name, **kw)
        print(f"started; log {info()[a.name]['logs']}")
        if a.wait:
            ok = await svc.wait_up(a.name, START_TIMEOUT[a.name])
            print("up" if ok else f"not up: {svc.errors.get(a.name, '')}")
            sys.exit(0 if ok else 1)

    asyncio.run(run())


if __name__ == "__main__":
    main()
