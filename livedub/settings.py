"""Settings that depend on the machine: where tools and models are, which ports, where the Linux-side services run.

Built-in defaults (DEFAULTS) follow the layout the setup scripts create, so a fresh install needs no settings file.
settings.json in the project root overrides any of them (only the keys you change; see settings.example.json and
docs/配置.md); LIVEDUB_SETTINGS points at another file instead. Relative paths are relative to the project root.

The speech services (ASR, dubbing) need Linux: on Windows they run in WSL 2, elsewhere natively. Their paths are Linux
paths; "{home}" in them stands for linux.home (default ~/live-dub, where the setup scripts put venvs and models).
"""
import copy
import json
import os
import shlex
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ROOT = (Path(os.environ.get("LIVEDUB_HOME") or str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "LiveDub"))
        if FROZEN else RESOURCES)
ROOT.mkdir(parents=True, exist_ok=True)
WINDOWS = os.name == "nt"
EXE = ".exe" if WINDOWS else ""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # creationflags for helper processes on Windows

DEFAULTS = {
    "runs_dir": "outputs/live",          # one directory per session
    "logs_dir": "logs",                  # service logs
    "models_dir": "models",              # the download folder: translation models (GGUF), the native ASR model
    "github_mirror": "",                 # put in front of github.com download URLs (a mirror for slow networks);
                                         # the downloads of tools then offer it as a source
    "keep_media_runs": 5,                # sessions that keep their video chunks (the rest keep subtitles only)
    "run_in_background": False,          # after the desktop window closes, keep the services running (else they
                                         # stop once no session runs, which frees the GPU)
    "tools": {
        "ffmpeg": "",
        "ffprobe": "",
        "llama_server": "",              # "" = tools/llama.cpp/llama-server, then PATH
        "mediamtx": "",                  # "" = tools/mediamtx/mediamtx, then PATH (only for the OBS output)
        "js_runtime": "",                # for YouTube (yt-dlp): "" = deno or node, whichever is installed
    },
    "youtube": {
        "cookies": "",                   # a cookies.txt for sites that ask to sign in (YouTube's bot check)
        "cookies_from_browser": "",      # or read them from a browser: chrome, edge, firefox, ...
    },
    "ports": {"asr": 8091, "mt": 8081, "dub": 8092, "omni": 8093, "rtmp": 1935},
    "linux": {
        "runtime": "",                   # "wsl" or "native"; "" = wsl on Windows, native elsewhere
        "distro": "",                    # WSL distribution ("" = the default one)
        "home": "~/live-dub",            # venvs and models of the speech services (scripts/linux/setup_*.sh)
    },
    "asr": {
        "backend": "onnx" if WINDOWS else "nemo",
        "onnx_dir": "",                  # "" = parakeet-onnx/ in models_dir
        "python": "{home}/venv-asr/bin/python",
        "model": "{home}/models/parakeet-tdt-0.6b-v2.nemo",
        "speaker_model": "{home}/models/titanet-large.nemo",
        "device": "cpu",
        "threads": 8,
    },
    "dub": {
        "backend": "omni",               # omni (IndexTTS-2.5 on vLLM-Omni, fastest), local (IndexTTS in-process), voxcpm
        "python": "{home}/venv-omni/bin/python",
        "vllm": "{home}/venv-omni/bin/vllm",
        "omni_dir": "{home}/vllm-omni",
        "model": "{home}/models/IndexTTS-2.5",
        "deploy_config": "dubsvc/omni/indextts2_5_16g.yaml",   # project-relative
        "indextts_repo": "{home}/index-tts",                   # local backend only
        "voxcpm_model": "{home}/models/VoxCPM2",               # voxcpm backend only
        "warmup_ref": "",                # a short speech clip (Linux path) to warm the model up with; "" = none
        "args": [],                      # extra arguments for dubsvc/server.py
        "vram_gb": 6.5,                  # roughly what it needs, for the free-memory warning
    },
    "mt": {
        "default": "index-9b",           # a key of livedub.mt.MODELS (or of "models" here)
        "gpu_layers": 999,
        "ctx": 4096,
        "models": {},                    # add or override translation models, e.g. {"index-9b": {"path": "D:/x.gguf"}}
    },
    "online": {},                        # override or add online providers (livedub.mt_chat.PROVIDERS)
    "obs": {
        "vcodec": "auto",                # h264_nvenc when the GPU can, else libx264
        "stream": "livedub",             # OBS media source: rtmp://127.0.0.1:<rtmp>/live/<stream>
        "font_zh": "", "font_zh_bold": "", "font_en": "",   # "" = look for a CJK font on this system
    },
}


def settings_file() -> Path:
    return Path(os.environ.get("LIVEDUB_SETTINGS") or ROOT / "settings.json")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


class Invalid(RuntimeError):
    """settings.json is not what it should be: saving a setting would overwrite what was written in it."""


# why settings.json could not be read, in words for the UI ("" when it was): the backend then runs on the defaults
# instead of failing to start, which left the window waiting for it with no reason given
problem = ""


def _shape(data, base: dict, at: str = "") -> str:
    """Where `data` breaks the shape of `base` ("" when it does not): a group of settings that must be { } but is not,
    e.g. "linux": "~/live-dub", which made the backend fail to start like a syntax error did."""
    if not isinstance(data, dict):
        return f"{at} 需要是 {{ }}，不能是 {json.dumps(data, ensure_ascii=False)[:40]}" if at else "最外层需要是 { }"
    for k, v in base.items():
        where = _shape(data[k], v, f"{at}.{k}" if at else k) if isinstance(v, dict) and k in data else ""
        if where:
            return where
    return ""


def _read(f: Path) -> dict:
    hint = ""
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))   # Notepad and PowerShell 5 may write a BOM
        where = _shape(data, DEFAULTS)
        if not where:
            return data
    except json.JSONDecodeError as e:
        where = f"第 {e.lineno} 行第 {e.colno} 列附近"
        hint = "常见原因是少了逗号或引号，或者 Windows 路径中的 \\ 没有写成 \\\\ 或 /。"
    except UnicodeDecodeError:
        where = "需要保存为 UTF-8 编码"
    raise Invalid(f"settings.json 格式有误（{where}）：{f}。{hint}")


@lru_cache(maxsize=1)
def get() -> dict:
    global problem
    s, problem = copy.deepcopy(DEFAULTS), ""
    f = settings_file()
    if f.exists():
        try:
            s = _merge(DEFAULTS, _read(f))
        except Invalid as e:
            problem = (f"{e}现在用的是默认设置，改正后重启后端才会生效"
                       "（安装版：在任务管理器中结束 LiveDubBackend.exe，再重新打开声影同传）")
    home = s["linux"]["home"].rstrip("/")
    for sec in ("asr", "dub"):
        for k, v in s[sec].items():
            if isinstance(v, str):
                s[sec][k] = v.replace("{home}", home)
    return s


def reload() -> dict:
    get.cache_clear()
    return get()


def save(update: dict) -> None:
    """Merge `update` into settings.json (creating it) and reload."""
    f = settings_file()
    cur = _read(f) if f.exists() else {}   # Invalid: left as it is, for its owner to correct
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(_merge(cur, update), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)
    reload()


def path(p: str | os.PathLike) -> Path:
    """A setting's path: absolute as given (~ expanded), else relative to the project root."""
    p = Path(os.path.expanduser(str(p)))
    return p if p.is_absolute() else ROOT / p


def asr_dir() -> Path:
    """Where the native ASR model is: asr.onnx_dir, or parakeet-onnx/ in the download folder."""
    d = get()["asr"]["onnx_dir"]
    return path(d) if d else path(get()["models_dir"]) / "parakeet-onnx"


def python_argv(module: str) -> list[str]:
    """The frozen backend also dispatches worker modules; no system Python is required."""
    return [sys.executable, *([] if FROZEN else ["-m"]), module]


def asr_path(p: str | os.PathLike) -> str:
    return str(Path(p).resolve()) if get()["asr"]["backend"] == "onnx" else to_linux(p)


def runs_dir() -> Path:
    return path(get()["runs_dir"])


def logs_dir() -> Path:
    d = path(get()["logs_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def port(name: str) -> int:
    return int(get()["ports"][name])


def url(name: str) -> str:
    return f"http://127.0.0.1:{port(name)}"


# ---- tools ------------------------------------------------------------------------------------------------------
def tool(name: str) -> str | None:
    """Full path of a tool, or None when it is not installed. "" settings look in tools/<name>/ then on PATH."""
    s = get()["tools"]
    exe = {"llama_server": "llama-server", "mediamtx": "mediamtx"}.get(name, name)
    v = s.get(name) or ""
    if v:
        p = path(v)
        return str(p) if p.exists() else shutil.which(v)
    if name in ("llama_server", "mediamtx", "ffmpeg", "ffprobe", "deno"):
        sub = ROOT / "tools" / {"llama_server": "llama.cpp", "ffprobe": "ffmpeg"}.get(name, name)
        hits = sorted(sub.rglob(exe + EXE)) if sub.exists() else []
        if hits:
            return str(hits[0])
    return shutil.which(exe)


def ffmpeg() -> str:
    return tool("ffmpeg") or "ffmpeg"


def ffprobe() -> str:
    return tool("ffprobe") or "ffprobe"


def js_runtime() -> str | None:
    """yt-dlp's --js-runtimes value for YouTube (deno or node, or the one set in tools.js_runtime), or None."""
    want = get()["tools"]["js_runtime"]
    if want and ("/" in want or "\\" in want):          # a path to the runtime
        p = path(want)
        return f"{p.stem.lower()}:{p}" if p.exists() else None
    for name in ([want] if want else ["deno", "node"]):
        exe = tool(name)
        if exe:
            return f"{name}:{exe}"
    return None


FONT_CANDIDATES = {
    "zh_bold": ["C:/Windows/Fonts/msyhbd.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc", "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc",
                "/System/Library/Fonts/PingFang.ttc"],
    "zh": ["C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
           "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
           "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "/System/Library/Fonts/PingFang.ttc"],
    "en": ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
           "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Helvetica.ttc"],
}


def font(kind: str) -> str | None:
    """A font file for the OBS output's burnt-in subtitles: zh_bold, zh or en (the setting first)."""
    key = {"zh_bold": "font_zh_bold", "zh": "font_zh", "en": "font_en"}[kind]
    v = get()["obs"].get(key)
    cands = [v] if v else FONT_CANDIDATES[kind] + (FONT_CANDIDATES["zh"] if kind == "zh_bold" else [])
    return next((c for c in cands if c and path(c).exists()), None)


# ---- the Linux side (WSL on Windows) -----------------------------------------------------------------------------
def linux_runtime() -> str:
    r = get()["linux"]["runtime"]
    return r if r in ("wsl", "native") else ("wsl" if WINDOWS else "native")


def to_linux(p: str | os.PathLike) -> str:
    """A path on this machine as the Linux side sees it (C:\\x -> /mnt/c/x under WSL)."""
    p = Path(p).resolve()
    if linux_runtime() != "wsl":
        return p.as_posix()
    s = str(p).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    raise ValueError(f"WSL cannot see {p} (not on a drive letter)")


def sh(p: str) -> str:
    """Quote a Linux path for bash, keeping a leading ~/ expandable."""
    if p == "~" or p.startswith("~/"):
        return '"$HOME"' + (shlex.quote(p[1:]) if len(p) > 1 else "")
    return shlex.quote(p)


def linux_argv(script: str) -> list[str]:
    """argv that runs a bash script on the Linux side (WSL or here)."""
    if linux_runtime() == "wsl":
        d = get()["linux"]["distro"]
        return ["wsl.exe"] + (["-d", d] if d else []) + ["-e", "bash", "-c", script]
    return ["bash", "-c", script]


def linux_run(script: str, timeout: float = 30) -> subprocess.CompletedProcess:
    return subprocess.run(linux_argv(script), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW)


def obs_url() -> str:
    return f"rtmp://127.0.0.1:{port('rtmp')}/live/{get()['obs']['stream']}"


def secret_file(name: str) -> Path:
    return ROOT / ".secret" / f"{name}.json"


if __name__ == "__main__":   # print the effective settings
    print(json.dumps(get(), ensure_ascii=False, indent=2))
    print(f"# settings file: {settings_file()} ({'found' if settings_file().exists() else 'none, defaults'})",
          file=sys.stderr)
