"""Dub speed-up bench, vLLM-Omni path: DevDay B, every sentence with a known speaker (same set as tts_single_pass.py),
through the patched omni server (started by python -m livedub.server.services start dub, :8093) with target_s = source sentence length, i.e. the
single-pass duration control the service uses. Wall time includes HTTP + base64 of the reference clips.
Writes outputs/m0/tts/<tag>/<i>_omni.wav (with --save) and result.json.
Run with the index-tts venv from the project directory (on the Linux side):  python bench/tts_omni.py --tag omni [--save] [--natural]
"""
import argparse
import json
import re
import sys
from pathlib import Path

import soundfile as sf

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from dubsvc.omni_client import OmniBackend  # noqa: E402

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


ap = argparse.ArgumentParser()
ap.add_argument("--tag", default="omni")
ap.add_argument("--save", action="store_true")
ap.add_argument("--natural", action="store_true", help="no target_s (natural speed)")
a = ap.parse_args()
mt = json.loads((P / "outputs/m0/mt_v2_B.json").read_text(encoding="utf-8"))
sents, rows = mt["sentences"], mt["runs"]["q4"]["rows"]
segs = json.loads((P / "outputs/m0/diar_nemotron_B.json").read_text(encoding="utf-8"))["runs"]["low_1.04s"]["segments"]
audio, sr0 = sf.read(SEG / "B.48k.wav", dtype="float32")
orig = P / "outputs/m0/tts/_orig_v2"
orig.mkdir(parents=True, exist_ok=True)
out = P / "outputs/m0/tts" / a.tag
out.mkdir(parents=True, exist_ok=True)
todo = [(i, speaker_of(s["s"], s["e"], segs)) for i, s in enumerate(sents) if rows[i]["zh"]]
todo = [(i, k) for i, k in todo if k in REFS]


def emo_of(i):
    f = orig / f"{i:02d}.wav"
    if not f.exists():
        s = sents[i]
        sf.write(f, audio[int((s["s"] - 0.05) * sr0): int((s["e"] + 0.05) * sr0)], sr0)
    return str(f)


be = OmniBackend()
for i, k in todo[:2]:  # warm-up
    be.synth("大家好，欢迎收看今天的发布会", str(SEG / "ref_B" / f"spk{k}.wav"), emo_of(i), target_s=3.0)
res = []
for i, k in todo:
    zh = re.sub(r"[。．.]$", "", rows[i]["zh"])
    lang = "zhen" if re.search(r"[A-Za-z]", zh) else "zh"
    target = round(sents[i]["e"] - sents[i]["s"], 3)
    row = {"i": i, "speaker": REFS[k], "zh": zh, "target_s": target}
    try:
        o = be.synth(zh, str(SEG / "ref_B" / f"spk{k}.wav"), emo_of(i), target_s=None if a.natural else target, lang=lang)
        wav, sr = o.pop("wav"), o.pop("sr")
        if a.save:
            sf.write(out / f"{i:02d}_omni.wav", wav, sr)
        row.update(o, rtf=round(o["omni_s"] / o["raw_s"], 3))
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(row, ensure_ascii=False), flush=True)
    res.append(row)
ok = [r for r in res if "rtf" in r]
err = lambda got, r: (got - r["target_s"]) / r["target_s"]  # noqa: E731
unc = [r for r in ok if not r["clamped"]]
summ = {"tag": a.tag, "n": len(res), "failed": len(res) - len(ok), "natural": a.natural,
        "rtf_p50": pct([r["rtf"] for r in ok], .5), "rtf_p90": pct([r["rtf"] for r in ok], .9),
        "short_wall_s": [r["omni_s"] for r in ok if r["target_s"] < 2],
        "wall_total": round(sum(r["omni_s"] for r in ok), 1), "clamped": sum(r["clamped"] for r in ok),
        "factor_log_hits": sum(r["factor"] is not None for r in ok),
        "err_raw_p50_unclamped": pct([abs(err(r["raw_s"], r)) for r in unc], .5),
        "err_trim_p50_unclamped": pct([abs(err(r["audio_s"], r)) for r in unc], .5),
        "err_trim_signed_p50_unclamped": pct([err(r["audio_s"], r) for r in unc], .5),
        "err_trim_p50_all": pct([abs(err(r["audio_s"], r)) for r in ok], .5),
        "max_codes": max((r["n_codes"] or 0) for r in ok) if ok else None}
print("SUMMARY", json.dumps(summ, ensure_ascii=False), flush=True)
(out / "result.json").write_text(json.dumps({"summary": summ, "rows": res}, ensure_ascii=False, indent=1), encoding="utf-8")
