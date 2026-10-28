"""Environment check: what this machine has and lacks for each part (watching, ASR, translation, dubbing, OBS output),
with the command that fixes each gap (commands in `backticks`: the app shows them as code). The app's environment
page shows the same list (GET /api/doctor), with a download button instead of the command where an item names one
of livedub/install.py's downloads ("install").

usage: python -m livedub.doctor        (exit code 1 when something required is missing)
"""
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

from . import install, mt, mt_chat, settings

WSL = settings.linux_runtime() == "wsl"
LINUX_RUN = "wsl -e bash" if WSL else "bash"   # how the README tells you to run a scripts/linux/*.sh


def _version(argv: list[str]) -> str | None:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                           creationflags=settings.NO_WINDOW)
        out = (r.stdout or r.stderr).strip().splitlines()
        return out[0][:120] if r.returncode == 0 and out else None
    except (OSError, subprocess.SubprocessError):
        return None


def _pkg(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def item(group, label, ok, detail="", fix="", level="required", get=""):
    """level: required (the app cannot run without it), optional (one feature needs it), info. get: the download
    (livedub/install.py) that fixes it, when this machine can do it."""
    return {"group": group, "label": label, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix, "level": level,
            "install": get if not ok and get and install.supported(get) else ""}


def cmd(key: str) -> str:
    return f"`uv run python -m livedub.install {key}`"


def checks() -> list[dict]:
    s = settings.get()
    out = []
    g = "基础"
    out.append(item(g, "Python", sys.version_info >= (3, 12), platform.python_version(), "需要 Python 3.12 或更高版本（uv 会自动安装）"))
    ff = _version([settings.ffmpeg(), "-version"])
    out.append(item(g, "ffmpeg", ff and _version([settings.ffprobe(), "-version"]),
                    (ff or "").replace("ffmpeg version ", "").split(" ")[0],
                    f"运行 {cmd('ffmpeg')}，或用系统的包管理器安装（Linux：`sudo apt install ffmpeg`）", get="ffmpeg"))
    for pkg in ("yt-dlp", "streamlink"):
        v = _pkg(pkg)
        out.append(item(g, pkg, v, v or "", "在项目目录运行 `uv sync`"))
    js = settings.js_runtime()
    out.append(item("YouTube 播放", "JavaScript 运行时（Deno 或 Node.js）", js, js or "",
                    f"yt-dlp 处理 YouTube 的播放校验需要它：运行 {cmd('deno')}，或安装 Node.js", "optional", "deno"))
    yt = s["youtube"]
    if yt.get("cookies") or yt.get("cookies_from_browser"):
        ok = bool(yt.get("cookies_from_browser")) or settings.path(yt["cookies"]).exists()
        out.append(item("YouTube 播放", "登录 cookies", ok, yt.get("cookies_from_browser") or yt.get("cookies"),
                        f"settings.json 中 youtube.cookies 指向的文件不存在：{yt.get('cookies')}", "optional"))

    # the Linux side: one call checks every path the speech services need
    a, d = s["asr"], s["dub"]
    side = "WSL" if WSL else "Linux"
    native = a["backend"] == "onnx"
    paths = {"dub_py": d["python"]}
    if not native:
        paths.update(asr_py=a["python"], asr_model=a["model"], asr_spk=a["speaker_model"])
    if d["backend"] == "omni":
        paths.update(dub_vllm=d["vllm"], dub_model=d["model"], dub_aux=d["model"].rstrip("/") + "/hf_cache/bigvgan")
    elif d["backend"] == "local":
        paths.update(dub_repo=d["indextts_repo"], dub_model=d["model"])
    else:
        paths.update(dub_model=d["voxcpm_model"])
    from .server.services import linux_missing
    try:
        missing, err = set(linux_missing(paths)), None
    except RuntimeError as e:
        missing, err = set(paths), str(e)
    fix_side = ("先安装 WSL 2：在管理员 PowerShell 中运行 `wsl --install`，重启后按提示创建 Ubuntu" if WSL
                else "需要 bash")
    out.append(item("配音（可选）" if native else "识别", f"{side} 环境", err is None, "" if err else err,
                    fix_side, "optional" if native else "required"))
    asr_ok = err is None and not missing & {"asr_py", "asr_model", "asr_spk"}
    if native:
        from .asr import FILES
        model_dir = settings.asr_dir()
        ready = all((model_dir / f).is_file() for f in FILES)
        out.append(item("识别", "识别模型（Parakeet + TitaNet）", ready, str(model_dir) if ready else "",
                        f"运行 {cmd('asr')}（约 0.8 GB）", get="asr"))
    else:
        out.append(item("识别", "识别环境（Parakeet + TitaNet，CPU）", asr_ok,
                    a["model"] if asr_ok else "缺少：" + "、".join(
                        {"asr_py": "Python 环境", "asr_model": "Parakeet 模型", "asr_spk": "TitaNet 模型"}[k]
                        for k in ("asr_py", "asr_model", "asr_spk") if k in missing),
                        f"在项目目录运行 `{LINUX_RUN} scripts/linux/setup_asr.sh`（约 6 GB，需十几分钟）" if err is None
                        else f"需要先完成「{side} 环境」一项", get="asr-env" if err is None else ""))

    # translation: a local model (llama-server + GGUF) or an online provider; one of them is enough
    g = "翻译"
    gp = _gpu()
    llama = settings.tool("llama_server")
    out.append(item(g, "llama-server（本地翻译）", llama, llama or "", f"运行 {cmd('llama')}", "optional", "llama"))
    any_ready = False
    for key, m in mt.models().items():
        if m.get("api"):
            why = mt_chat.problem(m["api"], m.get("model", ""))
            any_ready |= why is None
            out.append(item(g, f"线上翻译：{m.get('label', key)}", why is None, "已配置" if why is None else "",
                            why or "", "optional"))
        else:
            f = install.gguf(key)
            ok = f.is_file()
            any_ready |= ok and bool(llama)
            size = f"（约 {m['bytes'] / 1e9:.1f} GB）" if m.get("bytes") else ""
            out.append(item(g, f"本地模型：{m.get('label', key)}", ok, str(f) if ok else "",
                            f"运行 {cmd('model:' + key)}{size}", "optional", f"model:{key}"))
    default = s["mt"]["default"]
    out.append(item(g, "至少一种翻译可用", any_ready, "",
                    "下载一个本地模型（需要 NVIDIA 显卡和约 5 GB 显存），或在「环境」页的「翻译」中填写线上接口的 API key",
                    get=f"model:{default}" if gp and f"model:{default}" in install.catalog() else ""))
    out.append(item("显卡", "NVIDIA 显卡", gp, f"{gp['name']}，显存 {gp['total_mb'] / 1024:.0f} GB" if gp else "",
                    "本地翻译和配音需要 NVIDIA 显卡；没有显卡时可使用线上翻译并关闭配音", "optional"))

    g = "配音（可选）"
    labels = {"dub_py": "Python 环境", "dub_vllm": "vLLM-Omni", "dub_model": "模型", "dub_aux": "模型的辅助文件",
              "dub_repo": "index-tts 代码"}
    dub_keys = [k for k in paths if k.startswith("dub_")]
    dub_ok = err is None and not missing & set(dub_keys)
    script = {"omni": "setup_dub.sh", "local": "setup_indextts.sh", "voxcpm": "setup_voxcpm.sh"}.get(d["backend"], "setup_dub.sh")
    out.append(item(g, f"配音环境（{d['backend']}）", dub_ok,
                    d["model"] if dub_ok else "缺少：" + "、".join(labels[k] for k in dub_keys if k in missing),
                    f"在项目目录运行 `{LINUX_RUN} scripts/linux/{script}`（约 20 GB，需要 NVIDIA 显卡和约 {d['vram_gb']:g} GB 显存）"
                    if err is None else f"需要先完成「{side} 环境」一项", "optional", "dub" if err is None else ""))

    g = "OBS 输出（可选）"
    mtx = settings.tool("mediamtx")
    out.append(item(g, "MediaMTX", mtx, mtx or "", f"运行 {cmd('mediamtx')}", "optional", "mediamtx"))
    fz = settings.font("zh_bold")
    out.append(item(g, "中文字体（用于烧录字幕）", fz, fz or "",
                    "安装中文字体（Linux：`sudo apt install fonts-noto-cjk`），或在 settings.json 的 obs.font_zh 中填写字体路径",
                    "optional"))
    return out


def _gpu() -> dict:
    from .server.services import gpu
    return gpu()


def summary(items: list[dict]) -> dict:
    req = [i for i in items if i["level"] == "required"]
    return {"ok": all(i["ok"] for i in req), "missing_required": [i["label"] for i in req if not i["ok"]],
            "missing_optional": [i["label"] for i in items if i["level"] == "optional" and not i["ok"]],
            "settings_file": str(settings.settings_file()), "settings_exists": settings.settings_file().exists()}


def main():
    items = checks()
    if "--json" in sys.argv:
        print(json.dumps({"items": items, **summary(items)}, ensure_ascii=False, indent=1))
        return
    group = None
    for i in items:
        if i["group"] != group:
            group = i["group"]
            print(f"\n[{group}]")
        mark = "OK " if i["ok"] else ("缺 " if i["level"] == "required" else "-- ")
        print(f"  {mark} {i['label']}" + (f"  {i['detail']}" if i["detail"] else ""))
        if i["fix"]:
            print(f"        -> {i['fix']}")
    s = summary(items)
    print("\n" + ("必需项已就绪。" if s["ok"] else "缺少必需项：" + "、".join(s["missing_required"])))
    print(f"设置文件：{s['settings_file']}" + ("" if s["settings_exists"] else "（文件不存在，正在使用默认值）"))
    sys.exit(0 if s["ok"] else 1)


if __name__ == "__main__":
    main()
