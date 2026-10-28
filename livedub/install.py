"""Downloads for the parts this machine runs: the ASR model, the local translation models and llama-server, ffmpeg,
Deno (YouTube), MediaMTX (OBS output); and the Linux-side environments, which scripts/linux/setup_*.sh install.

The app queues downloads (POST /api/downloads/<key>) and runs them one at a time; the status stream carries their
progress ("downloads"). A file comes from the first of its sources that answers, or from the one the user picked; a
break, a pause or a switch of source resumes from the partial file, and every file is checked against a SHA-256
pinned here (models) or published by GitHub (tools) before it is used, so every source gives the same file. Tools
come from GitHub, or from the mirror in settings github_mirror. Models go to the download folder (settings
models_dir), tools to tools/.

usage: uv run python -m livedub.install KEY [--source ID]
       KEY: asr, llama, model:index-9b, ffmpeg, deno, mediamtx
       ID:  huggingface, modelscope, hf-mirror, github, github-mirror
"""
import argparse
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import ssl
import subprocess
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import mt, settings

LLAMA_TAG = "b11260"
MEDIAMTX_TAG = "v1.21.1"
UA = {"User-Agent": "live-dub"}
SOURCES = {"huggingface": "Hugging Face", "modelscope": "魔搭 ModelScope（国内）", "hf-mirror": "hf-mirror 镜像（国内）",
           "github": "GitHub", "github-mirror": "GitHub 镜像"}
HF = {"huggingface": "https://huggingface.co", "hf-mirror": "https://hf-mirror.com"}

# the native ASR model, pinned (repo, revision, file there, file here, bytes, SHA-256): Parakeet TDT 0.6B v2 int8 ONNX
# and the TitaNet speaker model, both exported by sherpa-onnx
_PARAKEET = ("csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8", "1ab9323565ddb038682214b292f588070a538ce2")
ASR_FILES = [
    (*_PARAKEET, "encoder.int8.onnx", "encoder.int8.onnx", 652184296,
     "a32b12d17bbbc309d0686fbbcc2987b5e9b8333a7da83fa6b089f0a2acd651ab"),
    (*_PARAKEET, "decoder.int8.onnx", "decoder.int8.onnx", 7257753,
     "b6bb64963457237b900e496ee9994b59294526439fbcc1fecf705b31a15c6b4e"),
    (*_PARAKEET, "joiner.int8.onnx", "joiner.int8.onnx", 1739080,
     "7946164367946e7f9f29a122407c3252b680dbae9a51343eb2488d057c3c43d2"),
    (*_PARAKEET, "tokens.txt", "tokens.txt", 9384, "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d"),
    ("csukuangfj/speaker-embedding-models", "0743f301363dec56491a490f6d6cbc9d67f9a3bf", "nemo_en_titanet_large.onnx",
     "speaker.onnx", 101405493, "d51abcf31717ef28162f26acb9d44dd4127c3d44c9b8624f699f3425daca8e77"),
]
# GitHub-hosted tools: roughly how big (for the button; the download takes the real size from GitHub)
TOOL_SIZE = {"ffmpeg": 110e6, "deno": 45e6, "llama": 600e6, "mediamtx": 20e6}
SCRIPTS = {"dub": ("配音环境", 20e9), "asr-env": ("识别环境", 6e9)}   # roughly what they download
NET_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException)


class Stopped(Exception):
    """The user paused the download, or picked another source (job.switch says which)."""


# ---- what can be downloaded ---------------------------------------------------------------------------------------

def _arch() -> str:
    return {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower(), "")


def supported(key: str) -> bool:
    """Whether this machine can download `key` here (the tools come in builds for some systems only)."""
    system, x64 = platform.system(), _arch() == "x64"
    if key in ("ffmpeg", "deno"):
        return x64 and system in ("Windows", "Linux")
    if key in ("llama", "mediamtx"):
        return system in ("Windows", "Linux", "Darwin") and bool(_arch())
    if key in SCRIPTS:
        return True
    return key == "asr" or key.startswith("model:")


def model_sources(m: dict) -> list[str]:
    return [*(["huggingface"] if m.get("hf") else []), *(["modelscope"] if m.get("ms") else []),
            *(["hf-mirror"] if m.get("hf") else [])]


def tool_sources() -> list[str]:
    return ["github", *(["github-mirror"] if settings.get()["github_mirror"] else [])]


def catalog() -> dict[str, dict]:
    """Every download this machine can do: label, approximate size in bytes, sources."""
    def entry(label, size, sources):
        return {"label": label, "size": int(size), "sources": [{"id": s, "label": SOURCES[s]} for s in sources]}
    out = {"asr": entry("识别模型", sum(f[4] for f in ASR_FILES), ["huggingface", "hf-mirror"]),
           **{k: entry(label, TOOL_SIZE[k], tool_sources())
              for k, label in (("llama", "llama-server"), ("ffmpeg", "ffmpeg"), ("deno", "Deno"),
                               ("mediamtx", "MediaMTX"))}}
    for key, m in mt.models().items():
        if not m.get("api") and not m.get("path") and model_sources(m):
            out[f"model:{key}"] = entry(m.get("label", key), m.get("bytes") or 0, model_sources(m))
    for key, (label, size) in SCRIPTS.items():
        out[key] = entry(label, size, [])
    return {k: v for k, v in out.items() if supported(k)}


def model_dir() -> Path:
    return settings.path(settings.get()["models_dir"])


def gguf(key: str) -> Path:
    """Where a local translation model's file is (or goes)."""
    return Path(mt.gguf_path(key) or "")


def downloaded() -> list[Path]:
    """The model files in the download folder (what moving the folder takes along), partial ones included."""
    files = [gguf(key) for key, m in mt.models().items() if not m.get("api") and not m.get("path")]
    if not settings.get()["asr"]["onnx_dir"]:
        files += [settings.asr_dir() / f[3] for f in ASR_FILES]
    return [q for p in files for q in (p, p.with_name(p.name + ".part")) if q.is_file()]


# ---- the jobs ------------------------------------------------------------------------------------------------------

class Job:
    def __init__(self, key: str, label: str, size: int, sources: list[str], source: str):
        self.key, self.label, self.sources = key, label, sources
        self.source = source or (sources[0] if sources else "")
        self.auto = not source            # on failure, go on to the next source
        self.state, self.message, self.error = "queued", "", ""
        self.done, self.total, self.speed = 0, size, 0.0
        self.base = 0                     # bytes of the files already finished
        self.pause = False
        self.switch = False
        self.dest: Path | None = None    # the move's new folder

    def view(self) -> dict:
        return {"label": self.label, "state": self.state, "done": self.done, "total": self.total,
                "speed": round(self.speed), "source": self.source, "auto": self.auto, "message": self.message,
                "error": self.error, "pausable": self.key not in SCRIPTS}

    def check(self):
        if self.pause or self.switch:
            raise Stopped


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()
_queue: list[str] = []
_worker: threading.Thread | None = None


def jobs() -> dict[str, dict]:
    with LOCK:
        return {k: j.view() for k, j in JOBS.items()}


def start(key: str, source: str = "") -> dict[str, dict]:
    """Queue `key` (or resume it), from `source` or the first that answers; for a running one, switch its source.
    A local translation model brings llama-server along when it is missing: the model cannot run without it."""
    items = catalog()
    if key not in items:
        raise ValueError("没有这个下载项")
    if source and source not in [s["id"] for s in items[key]["sources"]]:
        raise ValueError("没有这个下载来源")
    with LOCK:
        j = JOBS.get(key)
        if j and j.state in ("queued", "running"):
            if source and source != j.source:
                j.source, j.auto, j.switch = source, False, j.state == "running"
            return {k: v.view() for k, v in JOBS.items()}
        if key in SCRIPTS and any(o.state == "running" and k in SCRIPTS for k, o in JOBS.items()):
            raise RuntimeError("已有一个环境正在安装，请等它完成后再试")
        busy = {k for k, o in JOBS.items() if o.state in ("queued", "running")}
        if key.startswith("model:") and "llama" in items and "llama" not in busy and not settings.tool("llama_server"):
            _add("llama", items["llama"], "")
        _add(key, items[key], source)
        return {k: v.view() for k, v in JOBS.items()}


def _add(key: str, it: dict, source: str):
    """(with LOCK held)"""
    global _worker
    JOBS[key] = Job(key, it["label"], it["size"], [s["id"] for s in it["sources"]], source)
    if key in SCRIPTS:   # an environment install runs on its own: it takes long and cannot pause
        threading.Thread(target=_run, args=(key,), daemon=True).start()
        return
    _queue.append(key)
    if _worker is None:
        _worker = threading.Thread(target=_work, daemon=True)
        _worker.start()


def pause(key: str) -> dict[str, dict]:
    with LOCK:
        j = JOBS.get(key)
        if j and key in _queue:
            _queue.remove(key)
            j.state, j.speed = "paused", 0.0
        elif j and j.state == "running" and key not in SCRIPTS:
            j.pause = True
        return {k: v.view() for k, v in JOBS.items()}


def move(dest: Path) -> dict[str, dict]:
    """Move the downloaded models to `dest` and make it the download folder (queued after the running downloads)."""
    with LOCK:
        if "move" in JOBS and JOBS["move"].state in ("queued", "running"):
            raise RuntimeError("正在移动已下载的模型，请稍候")
        _add("move", {"label": "移动已下载的模型", "size": sum(p.stat().st_size for p in downloaded()),
                      "sources": []}, "")
        JOBS["move"].dest = dest
        return {k: v.view() for k, v in JOBS.items()}


def _work():
    global _worker
    while True:
        with LOCK:
            if not _queue:
                _worker = None   # under the lock: the next _add starts a new worker
                return
            key = _queue.pop(0)
        _run(key)


def _run(key: str):
    j = JOBS[key]
    j.state, j.error = "running", ""
    run = {"asr": _asr, "llama": _llama, "ffmpeg": _ffmpeg, "deno": _deno, "mediamtx": _mediamtx, "move": _move,
           "dub": _script, "asr-env": _script}.get(key, _model)
    while True:
        j.base, j.done, j.message = 0, 0, "正在连接…"
        try:
            run(j)
            j.state, j.done = "done", j.total
        except Stopped:
            if j.switch:   # another source was picked: start over from it, resuming the partial file
                j.switch = False
                continue
            j.state, j.pause = "paused", False
        except Exception as e:   # noqa: BLE001 — every failure ends the job with its reason
            j.state, j.error = "failed", str(e) or type(e).__name__
        break
    j.speed, j.message = 0.0, ""


# ---- downloading one file ------------------------------------------------------------------------------------------

def get_json(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def fetch(j: Job, url_of, dest: Path, size: int | None = None, sha: str | None = None) -> Path:
    """dest, from the job's source (in auto mode, the next source when one fails); resumes from dest.part.
    url_of(source) gives the file's URL there."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and (size is None or dest.stat().st_size == size):   # checked before it got its name
        j.base += dest.stat().st_size
        return dest
    part = dest.with_name(dest.name + ".part")
    need = (size or 0) - (part.stat().st_size if part.exists() else 0)
    free = shutil.disk_usage(dest.parent).free
    if need > free:
        raise RuntimeError(f"磁盘空间不足：还需要 {need / 1e9:.1f} GB，{dest.parent} 所在磁盘只剩 {free / 1e9:.1f} GB")
    errors = []
    while True:
        try:
            _get(j, url_of(j.source), part, size)
            if size is not None and part.stat().st_size != size:
                raise RuntimeError(f"大小不一致（{part.stat().st_size} / {size} 字节）")
            if sha:
                j.message = f"正在校验 {dest.name}…"
                if sha256(part) != sha:
                    part.unlink()
                    raise RuntimeError("文件校验不一致（SHA-256），已删除")
            part.replace(dest)
            j.base += dest.stat().st_size
            j.message = ""
            return dest
        except Stopped:
            raise
        except Exception as e:   # noqa: BLE001
            if not isinstance(e, NET_ERRORS + (RuntimeError,)):
                raise
            errors.append(f"{SOURCES.get(j.source, j.source)}：{_why(e)}")
            i = j.sources.index(j.source) if j.source in j.sources else len(j.sources)
            if not j.auto or i + 1 >= len(j.sources):
                raise RuntimeError("；".join(errors) + "。已下载的部分会保留，重试时接着下载") from e
            j.source = j.sources[i + 1]
            j.message = f"改从 {SOURCES[j.source]} 下载…"


def _why(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"服务器返回 {e.code}"
    if isinstance(e, urllib.error.URLError):
        return "无法连接"
    if isinstance(e, NET_ERRORS):
        return "连接超时或中断"
    return str(e)


def _get(j: Job, url: str, part: Path, size: int | None):
    """url -> part, appending to what part has; three tries in a row without progress fail. In auto mode a source
    that never answers fails at once, so the next one is tried without waiting through the retries."""
    tries, answered = 0, False
    while True:
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have >= size:
            if have > size:
                part.unlink()
                continue
            return
        req = urllib.request.Request(url, headers={**UA, **({"Range": f"bytes={have}-"} if have else {})})
        got = 0
        if not answered:
            j.message = f"正在连接 {SOURCES.get(j.source, j.source)}…"
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                answered = True
                if have and r.status != 206:      # the server ignored the range: start over
                    have = 0
                length = int(r.headers.get("Content-Length") or 0)
                if size is None and length:
                    j.total = j.base + have + length
                j.message = ""
                t0, n0 = time.time(), 0
                with open(part, "ab" if have else "wb") as f:
                    while b := r.read(1 << 20):
                        f.write(b)
                        got += len(b)
                        j.done = j.base + have + got
                        dt = time.time() - t0
                        if dt >= 1:
                            j.speed = (got - n0) / dt if not j.speed else 0.7 * j.speed + 0.3 * (got - n0) / dt
                            t0, n0 = time.time(), got
                        j.check()
            return
        except urllib.error.HTTPError as e:
            if e.code == 416 and have:            # nothing past what part has: it is complete or wrong
                return
            if e.code in (401, 403, 404, 410) or (not answered and j.auto and j.source != j.sources[-1]):
                raise
            tries = 0 if got else tries + 1
        except NET_ERRORS:
            if not answered and j.auto and j.source != j.sources[-1]:
                raise
            tries = 0 if got else tries + 1
        if tries >= 3:
            raise RuntimeError("多次连接失败")
        j.message = "连接中断，正在重试…"
        for _ in range(30):                       # wait 3 s, still answering pause
            j.check()
            time.sleep(0.1)


def release_assets(repo: str, tag: str) -> dict[str, tuple[str, str | None, int]]:
    """A GitHub release's files: name -> (URL, SHA-256 if published, bytes)."""
    rel = get_json(f"https://api.github.com/repos/{repo}/releases/{'latest' if tag == 'latest' else 'tags/' + tag}")
    return {a["name"]: (a["browser_download_url"], (a.get("digest") or "").removeprefix("sha256:") or None, a["size"])
            for a in rel["assets"]}


def extract(archive: Path, into: Path):
    into.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)
    else:
        with tarfile.open(archive) as t:
            t.extractall(into, filter="data")
    for p in into.rglob("*"):   # zip keeps no modes
        if p.is_file() and p.name in ("llama-server", "mediamtx", "ffmpeg", "ffprobe", "deno"):
            p.chmod(0o755)


def _tool(j: Job, repo: str, tag: str, names: list[str], into: Path):
    """Release files `names` of a GitHub repo, unpacked into `into`."""
    j.message = "正在获取版本信息…"
    try:
        assets = release_assets(repo, tag)
    except NET_ERRORS as e:
        raise RuntimeError(f"无法从 GitHub 获取版本信息（{_why(e)}），请检查网络后重试") from e
    missing = [n for n in names if n not in assets]
    if missing:
        raise RuntimeError(f"{repo} {tag} 没有 {', '.join(missing)}")
    j.total = sum(assets[n][2] for n in names)
    tmp = settings.ROOT / ".tmp" / "downloads"
    for n in names:
        url, sha, size = assets[n]
        archive = fetch(j, lambda s, u=url: settings.get()["github_mirror"] + u if s == "github-mirror" else u,
                        tmp / n, size, sha)
        j.message = f"正在解压 {n}…"
        extract(archive, into)
        archive.unlink(missing_ok=True)


# ---- each download -------------------------------------------------------------------------------------------------

def _asr(j: Job):
    dest = settings.asr_dir()
    for repo, rev, remote, local, size, sha in ASR_FILES:
        url_of = (lambda s, r=repo, v=rev, f=remote: f"{HF[s]}/{r}/resolve/{v}/{f}")
        fetch(j, url_of, dest / local, size, sha)


def _model(j: Job):
    key = j.key.removeprefix("model:")
    m = mt.models()[key]

    def url_of(s):
        if s == "modelscope":
            return f"https://www.modelscope.cn/models/{m['ms']}/resolve/master/{m['file']}"
        return f"{HF[s]}/{m['hf']}/resolve/main/{m['file']}"
    fetch(j, url_of, gguf(key), m.get("bytes"), m.get("sha256"))


def driver_cuda() -> float | None:
    """The CUDA version the NVIDIA driver supports (e.g. 13.2), or None without an NVIDIA GPU."""
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=15,
                             creationflags=settings.NO_WINDOW).stdout
        m = re.search(r"CUDA Version:\s*([\d.]+)", out)
        return float(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def llama_files(tag: str = LLAMA_TAG) -> tuple[str, list[str]]:
    """The llama.cpp build for this machine: (variant, release files)."""
    system, arch, cuda = platform.system(), _arch(), driver_cuda()
    variant = "cuda" if cuda and arch == "x64" and system != "Darwin" else ("metal" if system == "Darwin" else "vulkan")
    if system == "Windows":
        if variant == "cuda":
            cv = "13.4" if (cuda or 0) >= 13 else "12.4"
            return variant, [f"llama-{tag}-bin-win-cuda-{cv}-x64.zip", f"cudart-llama-bin-win-cuda-{cv}-x64.zip"]
        return variant, [f"llama-{tag}-bin-win-vulkan-x64.zip"]
    if system == "Linux":
        if variant == "cuda":
            cv = "13.4" if (cuda or 0) >= 13 else "12.8"
            return variant, [f"llama-{tag}-bin-ubuntu-cuda-{cv}-x64.tar.gz",
                             f"cudart-llama-{tag}-bin-ubuntu-cuda-{cv}-x64.tar.gz"]
        return variant, [f"llama-{tag}-bin-ubuntu-vulkan-{arch}.tar.gz"]
    return variant, [f"llama-{tag}-bin-macos-{arch}.tar.gz"]


def _llama(j: Job):
    variant, names = llama_files()
    into = settings.ROOT / "tools" / "llama.cpp" / f"{LLAMA_TAG}-{variant}"
    _tool(j, "ggml-org/llama.cpp", LLAMA_TAG, names, into)
    if not next(into.rglob("llama-server" + settings.EXE), None):
        raise RuntimeError("解压后没有找到 llama-server")


def _ffmpeg(j: Job):
    name = "ffmpeg-master-latest-" + ("win64-lgpl.zip" if settings.WINDOWS else "linux64-lgpl.tar.xz")
    into = settings.ROOT / "tools" / "ffmpeg"
    shutil.rmtree(into, ignore_errors=True)   # one build at a time: the lookup takes the first it finds
    _tool(j, "BtbN/FFmpeg-Builds", "latest", [name], into)


def _deno(j: Job):
    name = "deno-x86_64-" + ("pc-windows-msvc.zip" if settings.WINDOWS else "unknown-linux-gnu.zip")
    _tool(j, "denoland/deno", "latest", [name], settings.ROOT / "tools" / "deno")


def _mediamtx(j: Job):
    system = platform.system().lower()
    arch = {"x64": "amd64", "arm64": "arm64"}[_arch()]
    name = f"mediamtx_{MEDIAMTX_TAG}_{system}_{arch}." + ("zip" if system == "windows" else "tar.gz")
    _tool(j, "bluenviron/mediamtx", MEDIAMTX_TAG, [name], settings.ROOT / "tools" / "mediamtx" / MEDIAMTX_TAG)


def _script(j: Job):
    """A Linux-side environment, by its idempotent setup script; the log is the progress."""
    import psutil

    from .server import procs
    key, backend = j.key, settings.get()["dub"]["backend"]
    script = ("setup_asr.sh" if key == "asr-env" else
              {"local": "setup_indextts.sh", "voxcpm": "setup_voxcpm.sh"}.get(backend, "setup_dub.sh"))
    path = settings.RESOURCES / "scripts" / "linux" / script
    log = settings.logs_dir() / f"setup-{key}.log"
    argv = settings.linux_argv(f"export LIVEDUB_HOME={settings.sh(settings.get()['linux']['home'])}; "
                               f"bash {settings.sh(settings.to_linux(path))}")
    j.message = f"正在安装{j.label}，需要较长时间，可以离开此页面"
    pid = procs.spawn(argv, log, cwd=settings.ROOT)
    if psutil.Process(pid).wait() != 0:
        raise RuntimeError(f"安装失败，详见日志 {log}")


def _move(j: Job):
    """The downloaded models into the job's folder (renamed on the same disk, copied otherwise); then that folder is
    the download folder."""
    old, dest = model_dir(), j.dest
    assert dest is not None
    dest.mkdir(parents=True, exist_ok=True)
    for p in downloaded():
        rel = p.relative_to(old) if p.is_relative_to(old) else Path(p.name)
        to = dest / rel
        to.parent.mkdir(parents=True, exist_ok=True)
        j.message = f"正在移动 {p.name}…"
        try:
            os.replace(p, to)
        except OSError:   # another disk: copy, then delete
            with open(p, "rb") as src, open(to, "wb") as out:
                while b := src.read(1 << 22):
                    out.write(b)
                    j.done += len(b)
            p.unlink()
            continue
        j.done += to.stat().st_size
    settings.save({"models_dir": str(dest)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", help="asr, llama, model:<key>, ffmpeg, deno, mediamtx")
    ap.add_argument("--source", default="", choices=["", *SOURCES], help="where from (default: the first that answers)")
    a = ap.parse_args()
    if a.key not in catalog() or a.key in SCRIPTS:
        raise SystemExit(f"无法下载 {a.key}；可下载：{', '.join(k for k in catalog() if k not in SCRIPTS)}")
    start(a.key, a.source)
    last = ""
    while (j := JOBS[a.key]).state in ("queued", "running"):
        line = f"{j.label} {j.done / 2**20:8.0f} / {j.total / 2**20:.0f} MB {j.speed / 2**20:5.1f} MB/s  " \
               f"{SOURCES.get(j.source, '')} {j.message}"
        if line != last:
            print("\r" + line[:110].ljust(110), end="", flush=True)
            last = line
        time.sleep(0.5)
    print()
    if j.state != "done":
        raise SystemExit(j.error or "未完成")
    print("完成")


if __name__ == "__main__":
    main()
