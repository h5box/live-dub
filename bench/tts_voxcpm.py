"""VoxCPM2 dubbing bench: DevDay B, every sentence with a known speaker (same set as tts_omni.py), in process through
dubsvc.voxcpm_backend with target_s = source sentence length. RTF = generation wall time / untrimmed output length.
Writes outputs/m0/tts/<tag>/<i>_vox.wav (with --save) and result.json.
Run with ~/live-dub/venv-voxcpm from the project directory (on the Linux side):
  python bench/tts_voxcpm.py --tag voxcpm2 [--save] [--steps 10] [--no-optimize] [--limit N]
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import soundfile as sf

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from dubsvc.voxcpm_backend import VoxCPMBackend  # noqa: E402

SEG = Path.home() / "live-dub/seg"
REFS = {0: "Sam", 1: "Tejal", 2: "Romain", 3: "Codex voice"}


def speaker_of(s, e, segs):
    ov = {}
    for s2, e2, k in segs:
        o = min(e, e2) - max(s, s2)
        if o > 0:
            ov[k] = ov.get(k, 0) + o
    return max(ov, key=ov.get) if ov else None


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else None


def gpu_mib():
    q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int(q.stdout.strip().splitlines()[0])


ap = argparse.ArgumentParser()
ap.add_argument("--tag", default="voxcpm2")
ap.add_argument("--save", action="store_true")
ap.add_argument("--steps", type=int, default=10)
ap.add_argument("--cfg", type=float, default=2.0)
ap.add_argument("--no-optimize", action="store_true")
ap.add_argument("--no-normalize", action="store_true")
ap.add_argument("--limit", type=int, default=0)
a = ap.parse_args()
mt = json.loads((P / "outputs/m0/mt_v2_B.json").read_text(encoding="utf-8"))
sents, rows = mt["sentences"], mt["runs"]["q4"]["rows"]
segs = json.loads((P / "outputs/m0/diar_nemotron_B.json").read_text(encoding="utf-8"))["runs"]["low_1.04s"]["segments"]
out = P / "outputs/m0/tts" / a.tag
out.mkdir(parents=True, exist_ok=True)
todo = [(i, speaker_of(s["s"], s["e"], segs)) for i, s in enumerate(sents) if rows[i]["zh"]]
todo = [(i, k) for i, k in todo if k in REFS]
if a.limit:
    todo = todo[: a.limit]

base = gpu_mib()
t = time.time()
be = VoxCPMBackend(Path.home() / "models/VoxCPM2", steps=a.steps, cfg=a.cfg, normalize=not a.no_normalize,
                   optimize=not a.no_optimize)
load_s = time.time() - t
t = time.time()
for i, k in todo[:3]:  # warm-up (compile, if on)
    be.synth("大家好，欢迎收看今天的发布会", str(SEG / "ref_B" / f"spk{k}.wav"), target_s=3.0)
warm_s = time.time() - t
print(f"loaded in {load_s:.1f} s, warm-up {warm_s:.1f} s, sr {be.sr}, vram ~{gpu_mib() - base} MiB", flush=True)
res = []
for i, k in todo:
    zh = re.sub(r"[。．.]$", "", rows[i]["zh"])
    target = round(sents[i]["e"] - sents[i]["s"], 3)
    row = {"i": i, "speaker": REFS[k], "zh": zh, "target_s": target}
    try:
        t = time.time()
        o = be.synth(zh, str(SEG / "ref_B" / f"spk{k}.wav"), target_s=target)
        wall = time.time() - t
        wav, sr = o.pop("wav"), o.pop("sr")
        if a.save:
            sf.write(out / f"{i:02d}_vox.wav", wav, sr)
        row.update(o, wall_s=round(wall, 3), rtf=round(o["gen_s"] / max(o["raw_s"], 1e-3), 3))
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(row, ensure_ascii=False), flush=True)
    res.append(row)
ok = [r for r in res if "rtf" in r]
summ = {"tag": a.tag, "steps": a.steps, "cfg": a.cfg, "optimize": not a.no_optimize, "n": len(res),
        "failed": len(res) - len(ok), "load_s": round(load_s, 1), "warm_s": round(warm_s, 1),
        "vram_mib": gpu_mib() - base,
        "rtf_p50": pct([r["rtf"] for r in ok], .5), "rtf_p90": pct([r["rtf"] for r in ok], .9),
        "wall_p50": pct([r["wall_s"] for r in ok], .5), "wall_p90": pct([r["wall_s"] for r in ok], .9),
        "wall_max": max((r["wall_s"] for r in ok), default=None),
        "short_wall_s": [r["wall_s"] for r in ok if r["target_s"] < 2],
        "natural_over_target_p50": pct([r["natural_s"] / r["target_s"] for r in ok], .5),
        "clamped": sum(r["clamped"] for r in ok),
        "audio_over_target_p50": pct([r["audio_s"] / r["target_s"] for r in ok], .5)}
print("SUMMARY", json.dumps(summ, ensure_ascii=False), flush=True)
(out / "result.json").write_text(json.dumps({"summary": summ, "rows": res}, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
