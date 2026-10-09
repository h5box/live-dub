"""Download what runs on this machine (not in WSL): llama.cpp's server, the local translation models, MediaMTX.

  uv run python scripts/fetch.py llama            llama-server for this OS and GPU      -> tools/llama.cpp/
  uv run python scripts/fetch.py model index-9b   a translation model (GGUF)            -> models/ (settings models_dir)
  uv run python scripts/fetch.py mediamtx         RTMP server, only for the OBS output  -> tools/mediamtx/
  uv run python scripts/fetch.py all              llama + the default model

Versions are pinned to the ones this project was tested with (--tag to change). Downloads resume after a break and
are checked against the SHA-256 the host publishes. Mirrors: HF_ENDPOINT for Hugging Face (e.g. https://hf-mirror.com),
--github-mirror PREFIX for GitHub release files (the prefix is put in front of the github.com URL).
"""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from livedub import mt, settings  # noqa: E402

LLAMA_TAG = "b11260"
MEDIAMTX_TAG = "v1.21.1"
HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
UA = {"User-Agent": "live-dub-fetch"}


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def download(url, dest: Path, sha256=None, size=None):
    """url -> dest, resuming a partial dest.part; checks the size and SHA-256 when known."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (size is None or dest.stat().st_size == size) and (not sha256 or _sha(dest) == sha256):
        print(f"  已有 {dest.name}")
        return dest
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, 11):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={**UA, **({"Range": f"bytes={have}-"} if have else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if have and r.status != 206:      # the server ignored the range: start over
                    have = 0
                total = have + int(r.headers.get("Content-Length") or 0)
                t0, last = time.time(), 0.0
                with open(part, "ab" if have else "wb") as f:
                    while True:
                        b = r.read(1 << 20)
                        if not b:
                            break
                        f.write(b)
                        have += len(b)
                        if time.time() - last > 2:
                            last = time.time()
                            rate = (have / 2**20) / max(0.1, last - t0)
                            pct = f"{100 * have / total:5.1f}%" if total else ""
                            print(f"\r  {dest.name} {have / 2**20:8.0f} MB {pct} {rate:6.1f} MB/s", end="", flush=True)
            print()
            break
        except Exception as e:
            print(f"\n  第 {attempt} 次中断（{type(e).__name__}: {e}），10 秒后接着下")
            time.sleep(10)
    else:
        sys.exit(f"下载失败：{url}")
    if size is not None and part.stat().st_size != size:
        sys.exit(f"大小不对：{part}（{part.stat().st_size} / {size}），删掉它重下")
    if sha256 and _sha(part) != sha256:
        part.unlink()
        sys.exit(f"校验不对（SHA-256），已删掉，再运行一次重下：{dest.name}")
    part.replace(dest)
    return dest


def _sha(p: Path):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def extract(archive: Path, into: Path):
    into.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)
    else:
        with tarfile.open(archive) as t:
            t.extractall(into, filter="data")
    for p in into.rglob("*"):   # tar keeps modes; zip does not
        if p.is_file() and p.name in ("llama-server", "mediamtx"):
            p.chmod(0o755)


def release_assets(repo, tag, mirror):
    rel = get_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    return {a["name"]: (mirror + a["browser_download_url"], (a.get("digest") or "").removeprefix("sha256:") or None,
                        a["size"]) for a in rel["assets"]}


def driver_cuda():
    """The CUDA version the NVIDIA driver supports (e.g. 13.2), or None without an NVIDIA GPU."""
    try:
        out = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=15).stdout
        m = re.search(r"CUDA Version:\s*([\d.]+)", out)
        return float(m.group(1)) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def fetch_llama(a):
    system, arch = platform.system(), platform.machine().lower()
    x64 = arch in ("x86_64", "amd64")
    cuda = driver_cuda()
    variant = a.variant
    if variant == "auto":
        variant = "cuda" if cuda else ("metal" if system == "Darwin" else "vulkan")
    assets = release_assets("ggml-org/llama.cpp", a.tag, a.github_mirror)
    t = a.tag
    if system == "Windows":
        if variant == "cuda":
            cv = "13.4" if (cuda or 0) >= 13 else "12.4"
            names = [f"llama-{t}-bin-win-cuda-{cv}-x64.zip", f"cudart-llama-bin-win-cuda-{cv}-x64.zip"]
        else:
            names = [f"llama-{t}-bin-win-{'vulkan' if variant == 'vulkan' else 'cpu'}-x64.zip"]
    elif system == "Linux":
        if variant == "cuda":
            cv = "13.4" if (cuda or 0) >= 13 else "12.8"
            names = [f"llama-{t}-bin-ubuntu-cuda-{cv}-x64.tar.gz", f"cudart-llama-{t}-bin-ubuntu-cuda-{cv}-x64.tar.gz"]
        elif variant == "vulkan":
            names = [f"llama-{t}-bin-ubuntu-vulkan-{'x64' if x64 else 'arm64'}.tar.gz"]
        else:
            names = [f"llama-{t}-bin-ubuntu-{'x64' if x64 else 'arm64'}.tar.gz"]
    elif system == "Darwin":
        names = [f"llama-{t}-bin-macos-{'arm64' if arch == 'arm64' else 'x64'}.tar.gz"]
    else:
        sys.exit(f"不认识的系统 {system}：请自己下载 llama.cpp，在 settings.json 的 tools.llama_server 填路径")
    missing = [n for n in names if n not in assets]
    if missing:
        sys.exit(f"llama.cpp {t} 没有这些文件：{missing}（换 --tag 或 --variant）")
    into = ROOT / "tools" / "llama.cpp" / f"{t}-{variant}"
    print(f"llama.cpp {t}（{variant}{'，驱动支持 CUDA ' + str(cuda) if cuda else ''}）-> {into}")
    for n in names:
        url, sha, size = assets[n]
        extract(download(url, ROOT / ".tmp" / "downloads" / n, sha, size), into)
    exe = next(into.rglob("llama-server" + settings.EXE), None)
    if not exe:
        sys.exit("解压后没找到 llama-server")
    print(f"好了：{exe}")


def fetch_model(a):
    models = mt.models()
    keys = a.keys or [settings.get()["mt"]["default"]]
    for key in keys:
        m = models.get(key)
        if not m or m.get("api"):
            sys.exit(f"没有本地翻译模型 {key}（有：{', '.join(k for k, v in models.items() if not v.get('api'))}）")
        if m.get("path") and not a.force:
            print(f"{key}：settings.json 指定了路径 {m['path']}，不下载")
            continue
        info = get_json(f"{HF}/api/models/{m['hf']}?blobs=true")
        sib = next((s for s in info.get("siblings", []) if s["rfilename"] == m["file"]), None)
        if not sib:
            sys.exit(f"{m['hf']} 里没有 {m['file']}")
        dest = Path(mt.gguf_path(key))
        print(f"{key}：{m['hf']}/{m['file']}（{(sib.get('size') or 0) / 1e9:.1f} GB）-> {dest}")
        download(f"{HF}/{m['hf']}/resolve/main/{m['file']}", dest, (sib.get("lfs") or {}).get("sha256"), sib.get("size"))
        print(f"好了：{dest}")


def fetch_mediamtx(a):
    system, arch = platform.system().lower(), platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(arch, arch)
    t = a.tag
    name = f"mediamtx_{t}_{system}_{arch}." + ("zip" if system == "windows" else "tar.gz")
    assets = release_assets("bluenviron/mediamtx", t, a.github_mirror)
    if name not in assets:
        sys.exit(f"MediaMTX {t} 没有 {name}")
    into = ROOT / "tools" / "mediamtx" / t
    url, sha, size = assets[name]
    extract(download(url, ROOT / ".tmp" / "downloads" / name, sha, size), into)
    print(f"好了：{into / ('mediamtx' + settings.EXE)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--github-mirror", default="", help="prefix for github.com download URLs")
    sub = ap.add_subparsers(dest="what", required=True)
    p = sub.add_parser("llama", help="llama-server (local translation)")
    p.add_argument("--tag", default=LLAMA_TAG)
    p.add_argument("--variant", choices=["auto", "cuda", "vulkan", "cpu", "metal"], default="auto")
    p = sub.add_parser("model", help="local translation model(s)")
    p.add_argument("keys", nargs="*")
    p.add_argument("--force", action="store_true", help="download even if settings.json points elsewhere")
    p = sub.add_parser("mediamtx", help="MediaMTX (only for the OBS output)")
    p.add_argument("--tag", default=MEDIAMTX_TAG)
    p = sub.add_parser("all", help="llama + the default translation model")
    p.add_argument("--variant", choices=["auto", "cuda", "vulkan", "cpu", "metal"], default="auto")
    a = ap.parse_args()
    if a.what == "llama":
        fetch_llama(a)
    elif a.what == "model":
        fetch_model(a)
    elif a.what == "mediamtx":
        fetch_mediamtx(a)
    else:
        a.tag, a.keys, a.force = LLAMA_TAG, [], False
        fetch_llama(a)
        fetch_model(a)
    if a.what != "model":   # archives are unpacked; the model files are the downloads themselves
        shutil.rmtree(ROOT / ".tmp" / "downloads", ignore_errors=True)


if __name__ == "__main__":
    main()
