"""Environment check: what this machine has and lacks for each part (watching, ASR, translation, dubbing, OBS output),
with the command that fixes each gap. The app's services page shows the same list (GET /api/doctor).

usage: python -m livedub.doctor        (exit code 1 when something required is missing)
"""
import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

from . import mt, mt_chat, settings

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


def item(group, label, ok, detail="", fix="", level="required"):
    """level: required (the app cannot run without it), optional (one feature needs it), info."""
    return {"group": group, "label": label, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix, "level": level}


def checks() -> list[dict]:
    s = settings.get()
    out = []
    g = "基础"
    out.append(item(g, "Python", sys.version_info >= (3, 12), platform.python_version(), "需要 Python 3.12 以上（uv 会自动装）"))
    ff = _version([settings.ffmpeg(), "-version"])
    out.append(item(g, "ffmpeg", ff and _version([settings.ffprobe(), "-version"]),
                    (ff or "").replace("ffmpeg version ", "").split(" ")[0],
                    "Windows：winget install Gyan.FFmpeg；Linux：sudo apt install ffmpeg（或在 settings.json 的 tools 里填路径）"))
    for pkg in ("yt-dlp", "streamlink"):
        v = _pkg(pkg)
        out.append(item(g, pkg, v, v or "", "在项目目录运行 uv sync"))
    js = settings.js_runtime()
    out.append(item("看 YouTube", "JavaScript 运行时（Deno 或 Node.js）", js, js or "",
                    "yt-dlp 解 YouTube 的播放校验要用：装 Deno 或 Node.js（Windows：winget install DenoLand.Deno）", "optional"))
    yt = s["youtube"]
    if yt.get("cookies") or yt.get("cookies_from_browser"):
        ok = bool(yt.get("cookies_from_browser")) or settings.path(yt["cookies"]).exists()
        out.append(item("看 YouTube", "登录 cookies", ok, yt.get("cookies_from_browser") or yt.get("cookies"),
                        f"settings.json 里 youtube.cookies 指的文件不在：{yt.get('cookies')}", "optional"))

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
    fix_side = ("先装 WSL 2：在管理员 PowerShell 里运行 wsl --install，重启后按提示建好 Ubuntu" if WSL
                else "需要 bash")
    out.append(item("配音（可选）" if native else "识别", f"{side} 环境", err is None, "" if err else err,
                    fix_side, "optional" if native else "required"))
    asr_ok = err is None and not missing & {"asr_py", "asr_model", "asr_spk"}
    if native:
        from .asr import FILES
        model_dir = settings.path(a["onnx_dir"])
        ready = all((model_dir / f).is_file() for f in FILES)
        out.append(item("识别", "原生 CPU 识别（ONNX，无需 WSL）", ready, str(model_dir), "打开首次设置，下载识别模型"))
    else:
        out.append(item("识别", "识别环境（Parakeet + TitaNet，CPU）", asr_ok,
                    a["model"] if asr_ok else "缺：" + "、".join(
                        {"asr_py": "Python 环境", "asr_model": "Parakeet 模型", "asr_spk": "TitaNet 模型"}[k]
                        for k in ("asr_py", "asr_model", "asr_spk") if k in missing),
                        f"在项目目录运行 {LINUX_RUN} scripts/linux/setup_asr.sh（约 6 GB，十几分钟）"))

    # translation: a local model (llama-server + GGUF) or an online provider; one of them is enough
    g = "翻译"
    gp = _gpu()
    llama = settings.tool("llama_server")
    out.append(item(g, "llama-server（本地翻译）", llama, llama or "", "运行 uv run python scripts/fetch.py llama", "optional"))
    any_ready = False
    for key, m in mt.models().items():
        if m.get("api"):
            why = mt_chat.problem(m["api"], m.get("model", ""))
            any_ready |= why is None
            out.append(item(g, f"线上翻译：{m.get('label', key)}", why is None, "已配置" if why is None else "",
                            why or "", "optional"))
        else:
            f = Path(mt.gguf_path(key))
            ok = f.is_file()
            any_ready |= ok and bool(llama)
            out.append(item(g, f"本地模型：{m.get('label', key)}", ok, str(f) if ok else "",
                            f"运行 uv run python scripts/fetch.py model {key}（约 {m.get('size_gb', '?')} GB）", "optional"))
    out.append(item(g, "至少有一种翻译能用", any_ready, "",
                    "下载一个本地模型（要 N 卡、约 5 GB 显存），或在「环境」页填线上翻译的 API key"))
    out.append(item("显卡", "NVIDIA 显卡", gp, f"{gp['name']}，显存 {gp['total_mb'] / 1024:.0f} GB" if gp else "",
                    "本地翻译和配音要 NVIDIA 显卡；没有也能用：线上翻译、只出字幕", "optional"))

    g = "配音（可选）"
    labels = {"dub_py": "Python 环境", "dub_vllm": "vLLM-Omni", "dub_model": "模型", "dub_aux": "模型的辅助文件",
              "dub_repo": "index-tts 代码"}
    dub_keys = [k for k in paths if k.startswith("dub_")]
    dub_ok = err is None and not missing & set(dub_keys)
    script = {"omni": "setup_dub.sh", "local": "setup_indextts.sh", "voxcpm": "setup_voxcpm.sh"}.get(d["backend"], "setup_dub.sh")
    out.append(item(g, f"配音环境（{d['backend']}）", dub_ok,
                    d["model"] if dub_ok else "缺：" + "、".join(labels[k] for k in dub_keys if k in missing),
                    f"在项目目录运行 {LINUX_RUN} scripts/linux/{script}（约 20 GB，要 N 卡约 {d['vram_gb']:g} GB 显存）",
                    "optional"))

    g = "OBS 输出（可选）"
    mtx = settings.tool("mediamtx")
    out.append(item(g, "MediaMTX", mtx, mtx or "", "运行 uv run python scripts/fetch.py mediamtx", "optional"))
    fz = settings.font("zh_bold")
    out.append(item(g, "中文字体（烧字幕用）", fz, fz or "",
                    "装一个中文字体（Linux：sudo apt install fonts-noto-cjk），或在 settings.json 的 obs.font_zh 填路径",
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
    print("\n" + ("必需的都齐了。" if s["ok"] else "还缺必需的：" + "、".join(s["missing_required"])))
    print(f"设置文件：{s['settings_file']}" + ("" if s["settings_exists"] else "（没有，用的是默认值）"))
    sys.exit(0 if s["ok"] else 1)


if __name__ == "__main__":
    main()
