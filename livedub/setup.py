"""First-run downloads: fixed public assets only, resumable and verified before use."""
import asyncio
import shutil
import threading
from pathlib import Path

from . import settings

STATE = {"state": "idle", "message": "", "error": ""}
LOCK = threading.Lock()


def report():
    with LOCK:
        return dict(STATE)


def update(**values):
    with LOCK:
        STATE.update(values)


def install_native():
    from scripts.fetch import HF, download, get_json
    repo = "csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
    info = get_json(f"{HF}/api/models/{repo}?blobs=true")
    dest = settings.path(settings.get()["asr"]["onnx_dir"])
    from .asr import FILES
    for name in FILES[:-1]:
        obj = next(s for s in info["siblings"] if s["rfilename"] == name)
        update(message=f"正在下载并校验 {name}，中断后可重试续传")
        download(f"{HF}/{repo}/resolve/{info['sha']}/{name}", dest / name,
                 (obj.get("lfs") or {}).get("sha256"), obj.get("size"))
    info = get_json("https://api.github.com/repos/k2-fsa/sherpa-onnx/releases/tags/speaker-recongition-models")
    obj = next(a for a in info["assets"] if a["name"] == "nemo_en_titanet_large.onnx")
    update(message="正在下载并校验说话人模型")
    download(obj["browser_download_url"], dest / "speaker.onnx",
             (obj.get("digest") or "").removeprefix("sha256:") or None, obj["size"])


def install_tool(name):
    from scripts.fetch import download, extract, get_json
    if not settings.WINDOWS:
        raise RuntimeError("自动安装工具目前支持 Windows；Linux 请用系统包管理器安装")
    repo = "BtbN/FFmpeg-Builds" if name == "ffmpeg" else "denoland/deno"
    info = get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    asset = (next(a for a in info["assets"] if "win64-lgpl.zip" in a["name"]) if name == "ffmpeg" else
             next(a for a in info["assets"] if a["name"] == "deno-x86_64-pc-windows-msvc.zip"))
    update(message=f"正在下载并安装 {name}")
    archive = settings.ROOT / ".tmp" / "downloads" / asset["name"]
    download(asset["browser_download_url"], archive,
             (asset.get("digest") or "").removeprefix("sha256:") or None, asset["size"])
    extract(archive, settings.ROOT / "tools" / name)
    archive.unlink(missing_ok=True)


def install(component):
    try:
        if component == "subtitles":
            if not settings.tool("ffmpeg") or not settings.tool("ffprobe"):
                install_tool("ffmpeg")
            install_native()
        elif component == "youtube":
            install_tool("deno")
        elif component == "dub":
            from .server import procs
            script = settings.RESOURCES / "scripts" / "linux" / "setup_dub.sh"
            # The optional GPU environment is installed by the existing idempotent script.
            import psutil
            argv = settings.linux_argv(f"export LIVEDUB_HOME={settings.sh(settings.get()['linux']['home'])}; "
                                       f"bash {settings.sh(settings.to_linux(script))}")
            pid = procs.spawn(argv, settings.logs_dir() / "setup-dub.log", cwd=settings.ROOT)
            update(message="正在安装配音环境（约 20 GB），请保持程序运行")
            if psutil.Process(pid).wait() != 0:
                raise RuntimeError("配音安装失败，请查看 logs/setup-dub.log 后重试")
        update(state="done", message="安装完成，请检查环境", error="")
    except (Exception, SystemExit) as e:
        update(state="failed", error=f"安装未完成：{e}。检查网络与磁盘空间后重试，可继续未完成的下载。")


async def start(component, model_dir=None):
    if component not in ("subtitles", "youtube", "dub"):
        raise ValueError("未知的安装组件")
    if report()["state"] == "running":
        raise RuntimeError("已有安装任务正在进行")
    if component == "subtitles":
        values = {"backend": "onnx"}
        if model_dir:
            dest = Path(model_dir).expanduser()
            if not dest.is_absolute():
                raise ValueError("模型目录必须是完整路径")
            dest.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(dest).free < 1_000_000_000:
                raise ValueError("模型目录至少需要 1 GB 可用空间")
            values["onnx_dir"] = str(dest)
        settings.save({"asr": values})
    update(state="running", message="准备下载…", error="")
    asyncio.create_task(asyncio.to_thread(install, component))
    return report()
