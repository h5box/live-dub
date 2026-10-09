"""A session: a live link, a video link or a local video file, translated for watching in the app. ingest cuts the
source into chunks and writes the run's anchor; processor writes subtitles and dubs; the app's player shows the chunks
(as HLS, see playlist()) with them D seconds late. With "obs" on, playout also burns them into a stream for OBS
through MediaMTX (rebroadcasting is the user's call and risk). Each run lives in its own timestamped directory under
settings runs_dir (outputs/live); the state is in its run.json, so a restarted backend re-attaches to a session that is
still running. Presets are configs/*.json plus the user's own in configs/local/*.json (same name: the user's wins)."""
import asyncio
import csv
import functools
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil

from .. import mt, mt_chat, settings
from ..settings import ROOT
from . import procs
from .services import Services

RUNS = settings.runs_dir()
CONFIGS = settings.RESOURCES / "configs"
LOCAL_CONFIGS = ROOT / "configs" / "local"
NAME_RE = r"\w[\w.-]*"                  # a run directory / preset name, nothing else
OBS_URL = settings.obs_url()            # fixed: OBS keeps one media source across sessions
ROLES = ("ingest", "playout", "processor")


def preset_file(name: str) -> Path | None:
    if not re.fullmatch(NAME_RE, name):
        return None
    for d in (LOCAL_CONFIGS, CONFIGS):
        if (d / f"{name}.json").is_file():
            return d / f"{name}.json"
    return None


def list_configs() -> list[dict]:
    files = {p.stem: p for p in sorted(CONFIGS.glob("*.json"))}
    files.update({p.stem: p for p in sorted(LOCAL_CONFIGS.glob("*.json"))} if LOCAL_CONFIGS.is_dir() else {})
    models = mt.models()
    out = []
    for name in sorted(files, key=lambda n: (n != "default", files[n].parent == LOCAL_CONFIGS, n)):
        try:
            c = json.loads(files[name].read_text(encoding="utf-8"))
        except Exception:
            continue
        src = c.get("src", "")
        model = c.get("mt_model") or settings.get()["mt"]["default"]
        out.append({"name": name, "title": c.get("name", name), "src": src, "kind": source_kind(src),
                    "problem": source_problem(src) if src else None, "local": files[name].parent == LOCAL_CONFIGS,
                    "delay_s": c.get("delay_s", 30), "domain": c.get("domain", ""),
                    "mt_model": model, "mt_online": bool(models.get(model, {}).get("api"))})
    return out


def model_problem(key: str) -> str | None:
    """Why this translation model cannot run here, in words for the UI, or None."""
    m = mt.models().get(key)
    if not m:
        return f"没有翻译模型 {key}"
    if m.get("api"):
        return mt_chat.problem(m["api"], m.get("model", ""))
    if not Path(mt.gguf_path(key)).is_file():
        return f"模型文件还没下载（python scripts/fetch.py model {key}）"
    if not settings.tool("llama_server"):
        return "还没有 llama-server（python scripts/fetch.py llama）"
    return None


def list_models() -> list[dict]:
    """The translation models and whether each can run here: a local GGUF and llama-server, or an online key."""
    out = []
    for key, m in mt.models().items():
        why = model_problem(key)
        if m.get("api"):
            name = mt_chat.provider(m["api"]).get("model") or m.get("model")
            detail = f"线上，{name}" if name else "线上"
        else:
            detail = f"本地，约 {m['vram_gb']:g} GB 显存" if m.get("vram_gb") else "本地"
        out.append({"key": key, "label": m.get("label", key), "detail": detail, "online": bool(m.get("api")),
                    "ready": why is None, "note": why or "", "default": key == settings.get()["mt"]["default"]})
    return out


def source_kind(src: str) -> str:
    """link (a live or recorded stream behind a URL; ingest tells which) or file (a local video)."""
    return "link" if src.startswith(("http://", "https://")) else "file"


def source_problem(src: str) -> str | None:
    """Why this source cannot start, in words for the UI, or None."""
    if not src:
        return "没有填直播链接或视频文件"
    if source_kind(src) == "file" and not (ROOT / src).is_file():   # relative paths are from the project root
        return f"视频文件不在：{src}"
    return None


def playout_line(d: Path) -> dict:
    """The playout's latest status line in a run directory (PT, frames, dups, slate, events, max-late), or {}."""
    p = d / "playout.log"
    try:
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - 4096))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return {}
    for line in reversed(tail):
        m = re.search(r"PT ([\d.]+) \S+ frames (\d+) .*dups (\d+) slate (\d+) events (\d+) max-late (\d+) ms", line)
        if m:
            pt, frames, dups, slate, events, late = m.groups()
            return {"pt": float(pt), "frames": int(frames), "dups": int(dups), "slate": int(slate),
                    "events": int(events), "max_late_ms": int(late)}
    return {}


def read_anchor(d: Path) -> dict | None:
    """{"T0", "p0", "delay"}: written by the playout at its first frame; PT p airs at T0 + delay + (p - p0)."""
    try:
        return json.loads((d / "anchor.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_detail(name: str) -> dict | None:
    """One past (or the current) session for the history page: run.json, its subtitles (the last line per pt0, i.e.
    with the dub once it exists), the timing records and the anchor."""
    if not re.fullmatch(NAME_RE, name):
        return None
    d = RUNS / name
    try:
        info = json.loads((d / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    info.pop("pids", None)

    def jsonl(f: str) -> list[dict]:
        rows = []
        try:
            with open(d / f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass     # a half-written last line
        except OSError:
            pass
        return rows

    events = {}
    for e in jsonl("events.jsonl"):
        if "pt0" in e:
            events[e["pt0"]] = e
    return {"info": info, "events": sorted(events.values(), key=lambda e: e["pt0"]), "timing": jsonl("timing.jsonl"),
            "anchor": read_anchor(d)}


@functools.lru_cache(maxsize=256)
def _seg_start(path: str, size: int) -> float | None:
    """A chunk's own start PT (ffprobe): ffmpeg's segment list gives 0 for the first chunk of every ingest run."""
    try:
        out = subprocess.run([settings.ffprobe(), "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0",
                              path], capture_output=True, text=True, timeout=20,
                             creationflags=settings.NO_WINDOW).stdout.strip()
        return float(out)
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def _pdt(pt: float) -> str:
    """PT as an HLS program date (1970-01-01 + PT): the player reads the program time of any position from it."""
    ms = int(round(pt * 1000))
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms // 1000)) + f".{ms % 1000:03d}Z"


def playlist(name: str) -> str | None:
    """The run's chunks as an HLS media playlist for the app's player: EVENT while it grows from the first chunk,
    a sliding live playlist once old chunks are gone, ENDLIST when the source ended or the run is over. Each chunk
    carries its start PT as EXT-X-PROGRAM-DATE-TIME; a live restart (a new ingest run) is a discontinuity."""
    if not re.fullmatch(NAME_RE, name):
        return None
    d = RUNS / name
    buf = d / "buf"
    if not buf.is_dir():
        return None
    lists = sorted(buf.glob("list_run*.csv"), key=lambda f: int(f.stem[8:])) or [buf / "list.csv"]
    segs = []                                   # (file, start PT, end PT, run index)
    for k, f in enumerate(lists):
        try:
            rows = [r for r in csv.reader(f.read_text().splitlines()) if len(r) >= 3]
        except OSError:
            continue
        for j, r in enumerate(rows):
            start = float(r[1])
            if j == 0 and (buf / r[0]).exists():   # each run's first row says 0: ask the chunk
                first = _seg_start(str(buf / r[0]), (buf / r[0]).stat().st_size)
                start = first if first is not None else max(0.0, float(r[2]) - 1.0)
            segs.append((r[0], start, float(r[2]), k))
    kept = [x for x in segs if (buf / x[0]).exists()]
    try:
        state = json.loads((d / "run.json").read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError):
        state = None
    ended = (buf / "END").exists() or state in ("ended", "failed")
    out = ["#EXTM3U", "#EXT-X-VERSION:6",
           f"#EXT-X-TARGETDURATION:{max([1] + [int(-(-(e - s) // 1)) for _, s, e, _ in kept])}",
           f"#EXT-X-MEDIA-SEQUENCE:{int(kept[0][0][4:10]) if kept else 0}",
           f"#EXT-X-DISCONTINUITY-SEQUENCE:{kept[0][3] if kept else 0}", "#EXT-X-INDEPENDENT-SEGMENTS"]
    if kept and kept[0] == segs[0]:
        out.append("#EXT-X-PLAYLIST-TYPE:EVENT")
    run = kept[0][3] if kept else 0
    for f, s, e, k in kept:
        if k != run:
            out.append("#EXT-X-DISCONTINUITY"); run = k
        out += [f"#EXT-X-PROGRAM-DATE-TIME:{_pdt(s)}", f"#EXTINF:{e - s:.3f},", f]
    if ended:
        out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"


def media_file(name: str, kind: str, file: str) -> Path | None:
    """A chunk (seg_NNNNNN.ts) or a dub clip (dub_NNNNN.wav) of a run, if it exists."""
    pat = {"seg": (r"seg_\d{6}\.ts", "buf"), "clip": (r"dub_\d{5}\.wav", "clips")}[kind]
    if not re.fullmatch(NAME_RE, name) or not re.fullmatch(pat[0], file):
        return None
    p = RUNS / name / pat[1] / file
    return p if p.is_file() else None


def prune_buffers(keep: int | None = None):
    """Chunks are a cache for the player: only the newest `keep` runs hold theirs (each is a copy of what was watched)."""
    import shutil
    keep = settings.get()["keep_media_runs"] if keep is None else keep
    runs = sorted(d for d in RUNS.glob("*/buf") if d.is_dir())
    for d in runs[:-keep] if keep else runs:
        shutil.rmtree(d, ignore_errors=True)


class Session:
    def __init__(self, services: Services):
        self.svc = services
        self.info: dict | None = None   # contents of run.json for the current / last session
        self.dir: Path | None = None
        self.task: asyncio.Task | None = None
        self.reattach()

    # ---- persistence --------------------------------------------------------------------------------------
    def _save(self):
        (self.dir / "run.json").write_text(json.dumps(self.info, ensure_ascii=False, indent=1), encoding="utf-8")

    def _rel(self, d: Path | None = None) -> Path:
        """The run directory as the processes got it: relative to the project root when it is inside."""
        d = d or self.dir
        return d.relative_to(ROOT) if d.is_relative_to(ROOT) else d

    def _procs(self, d: Path | None = None) -> dict[str, list[psutil.Process]]:
        dq = re.escape(str(self._rel(d)))
        found = {r: [] for r in ROLES}
        for p in procs.find(rf"livedub\.(ingest|playout|processor) .*{dq}"):
            try:
                role = re.search(r"livedub\.(\w+)", " ".join(p.cmdline())).group(1)
                found[role].append(p)
            except Exception:
                pass
        return found

    def reattach(self):
        runs = sorted(RUNS.glob("*/run.json")) if RUNS.exists() else []
        for rj in reversed(runs):
            info = json.loads(rj.read_text(encoding="utf-8"))
            self.dir, self.info = rj.parent, info
            if info.get("state") in ("running", "starting"):
                if any(self._procs().values()):
                    info["state"] = "running"
                    info.setdefault("log", []).append([time.time(), "后端重启后接回本场"])
                else:
                    info["state"] = "ended"
                    info["ended"] = info.get("ended") or time.time()
                    info.setdefault("log", []).append([time.time(), "后端重启时发现本场进程已不在"])
                self._save()
            return

    # ---- start --------------------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return bool(self.info) and self.info.get("state") in ("starting", "running", "stopping")

    def step(self, msg: str):
        self.info.setdefault("log", []).append([time.time(), msg])
        self._save()

    async def start(self, preset: str = "default", src: str | None = None, delay: float | None = None,
                    dub: bool = True, obs: bool = False, mt_model: str | None = None) -> dict:
        """src: a URL or a local video path (default: the preset's own). The preset brings the rest (glossary, names,
        hotwords, subtitle placement); delay, dub, obs and mt_model override it for this run."""
        if self.active:
            raise RuntimeError("已经有一场在跑，先停掉")
        pf = preset_file(preset)
        if not pf:
            raise RuntimeError(f"没有预设 {preset}")
        base = json.loads(pf.read_text(encoding="utf-8"))
        src = (src or base.get("src") or "").strip().strip('"')
        problem = source_problem(src)
        if problem:
            raise RuntimeError(problem)
        cfg = dict(base, src=src, dub=bool(dub), obs=bool(obs))
        if src != base.get("src"):
            cfg.pop("src_ss", None)        # the preset's start offset belongs to its own recording
        if delay is not None:
            cfg["delay_s"] = float(delay)
        cfg["mt_model"] = mt_model or cfg.get("mt_model") or settings.get()["mt"]["default"]
        why = model_problem(cfg["mt_model"])
        if why:
            raise RuntimeError(f"翻译模型 {cfg['mt_model']} 用不了：{why}")
        yt = settings.get()["youtube"]   # for sites that ask to sign in; the preset's own cookies file wins
        if yt.get("cookies") and not cfg.get("cookies"):
            cfg["cookies"] = str(settings.path(yt["cookies"]))
        if yt.get("cookies_from_browser") and not cfg.get("cookies"):
            cfg["cookies_from_browser"] = yt["cookies_from_browser"]
        kind = source_kind(src)
        title = base.get("name", preset) if src == base.get("src") else (Path(src).name if kind == "file" else src)
        name = time.strftime("%Y%m%d-%H%M%S") + "-" + (preset if preset != "default" else kind)
        await asyncio.to_thread(prune_buffers)
        self.dir = RUNS / name
        (self.dir / "clips").mkdir(parents=True, exist_ok=True)
        (self.dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
        self.info = {"name": name, "preset": preset, "title": title, "src": src, "kind": kind, "dir": str(self.dir),
                     "delay_s": cfg.get("delay_s", 30), "dub": cfg["dub"], "obs": cfg["obs"],
                     "obs_url": OBS_URL if cfg["obs"] else None, "mt_model": cfg["mt_model"],
                     "media": f"/api/sessions/{name}/media/index.m3u8",
                     "state": "starting", "created": time.time(), "log": []}
        self._save()
        self.task = asyncio.create_task(self._start(cfg))
        return self.info

    async def _start(self, cfg: dict):
        try:
            await self._ensure_services(cfg)
            if self.info["state"] != "starting":
                return
            await asyncio.to_thread(self._launch, cfg)
            self.info["state"] = "running"
            self.info["started"] = time.time()
            self.step(f"已开始，约 {cfg.get('delay_s', 30):g} 秒后开始播放"
                      + (f"；OBS 媒体源地址 {OBS_URL}" if cfg.get("obs") else ""))
        except Exception as e:
            self.step(f"开播失败：{e}")
            await asyncio.to_thread(self._kill_all)
            self.info["state"] = "failed"
            self.info["ended"] = time.time()
            self._save()

    async def _ensure_services(self, cfg: dict):
        s = (await self.svc.refresh())["services"]
        if cfg.get("obs") and not s["mediamtx"]["up"]:   # only the OBS output needs it
            self.step("启动 MediaMTX")
            await self.svc.start("mediamtx")
            if not await self.svc.wait_up("mediamtx", 30):
                raise RuntimeError("MediaMTX 起不来")
        want_boost = list(cfg.get("asr_boost", [])) if settings.get()["asr"]["backend"] == "nemo" else []
        if s["asr"]["up"] and s["asr"].get("boost", []) != want_boost:
            self.step("识别服务的热词和本场配置不同，用本场配置重启识别")
            await self.svc.stop("asr")
            await self.svc.wait_down("asr", 30)
            s["asr"]["up"] = False
        if not s["asr"]["up"]:
            self.step("启动识别服务（约 30 秒）")
            await self.svc.start("asr", config=self.dir / "config.json")
            if not await self.svc.wait_up("asr", 150):
                raise RuntimeError(self.svc.errors.get("asr") or "识别服务起不来，看日志 logs/asr.log")
        model = cfg["mt_model"]
        online = bool(mt.models().get(model, {}).get("api"))
        if online:   # online translation: the processor calls the API, no llama-server
            self.step(f"翻译用线上模型 {model}（整场对话做上下文）")
        elif s["mt"]["up"] and s["mt"].get("model") != model:
            self.step(f"翻译服务跑的不是 {model}，重启")
            await self.svc.stop("mt")
            await self.svc.wait_down("mt", 30)
            s["mt"]["up"] = False
        if not s["mt"]["up"] and not online:
            self.step(f"启动翻译服务 {model}")
            await self.svc.start("mt", model=model)
            if not await self.svc.wait_up("mt", 180, lambda x: x.get("model") == model):
                raise RuntimeError(self.svc.errors.get("mt") or "翻译服务起不来，看日志 logs/mt.log")
        if cfg["dub"] and not s["dub"]["up"]:
            self.step("配音服务没开：这场只出字幕（要配音先在「环境」页启动，约 1.5–3 分钟）")

    def _launch(self, cfg: dict):
        d, rel = self.dir, self.dir.relative_to(ROOT) if self.dir.is_relative_to(ROOT) else self.dir
        link = source_kind(cfg["src"]) == "link"
        py = [sys.executable, *([] if settings.FROZEN else ["-m"])]
        # the source comes from config.json, so a user's path or URL never goes through a command line
        ing = py + ["livedub.ingest", "--src-config", str(rel / "config.json"), str(rel / "buf"),
                    "--delay", str(cfg.get("delay_s", 30))]
        if link:   # live or a recording behind a URL; ingest tells them apart and paces the recording
            ing += ["--live", "--keep-s", str(cfg.get("keep_s", 1800))]
        proc = py + ["livedub.processor", str(rel / "buf"), str(rel / "events.jsonl"), str(rel / "config.json"),
                     "--clips", str(rel / "clips"), "--keep-dubs", str(cfg.get("keep_dubs", 600 if link else 0))]
        roles = [("ingest", ing), ("processor", proc)]
        if cfg.get("obs"):
            play = py + ["livedub.playout", str(rel / "buf"), str(rel / "events.jsonl"), OBS_URL,
                         "--delay", str(cfg.get("delay_s", 30)), "--tag", cfg.get("tag", "AI 翻译配音")]
            for k in ("sub_bottom", "sub_box", "tag_corner"):
                if cfg.get(k) is not None:
                    play += ["--" + k.replace("_", "-"), str(cfg[k])]
            roles.insert(1, ("playout", play))
        pids = {}
        for role, argv in roles:
            pids[role] = procs.spawn(argv, d / f"{role}.log", d / f"{role}.err", cwd=ROOT)
            if role == "ingest":
                time.sleep(1)
        self.info["pids"] = pids
        self._save()

    # ---- stop ---------------------------------------------------------------------------------------------
    def _kill_all(self) -> int:
        if not self.dir:
            return 0
        dq = re.escape(str(self._rel()))
        victims = procs.find(dq, ("python", "uv", "cmd", "ffmpeg", "ffprobe", "streamlink", "yt-dlp"))
        pids = set((self.info or {}).get("pids", {}).values())
        victims += [p for p in (psutil.Process(x) for x in pids if procs.alive(x)) if p not in victims]
        return procs.kill(victims, tree=True)

    async def stop(self) -> dict:
        if not self.active:
            raise RuntimeError("现在没有在跑的场次")
        if self.task and not self.task.done():
            self.task.cancel()
        self.info["state"] = "stopping"
        self.step("停播")
        n = await asyncio.to_thread(self._kill_all)
        await asyncio.sleep(1)
        n += await asyncio.to_thread(self._kill_all)   # stragglers spawned while the first sweep ran
        self.info["state"] = "ended"
        self.info["ended"] = time.time()
        self.step(f"已停，结束了 {n} 个进程")
        return self.info

    # ---- monitoring ---------------------------------------------------------------------------------------
    def health(self) -> dict:
        if not self.info or not self.dir:
            return {}
        running = {}
        mem = {}
        if self.active:
            for role, ps in self._procs().items():
                running[role] = bool(ps)
                try:
                    mem[role] = int(sum(p.memory_info().rss for p in ps) / 2**20)
                except psutil.NoSuchProcess:
                    pass
            grace = time.time() - (self.info.get("started") or time.time()) < 30   # uv takes a moment to spawn python
            if self.info["state"] == "running" and not grace and not any(running.values()):
                self.info["state"] = "ended"
                self.info["ended"] = time.time()
                self.step("三个进程都已退出，本场结束")
        return {"running": running, "mem_mb": mem, "anchor": read_anchor(self.dir),
                "playout": playout_line(self.dir) if self.info.get("obs", True) else {}}

