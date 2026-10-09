"""Pick an 8 s voice reference per speaker from a diarized + transcribed segment and cut it with ffmpeg.

A window qualifies if it lies inside one speaker turn, at least MARGIN s away from the turn edges (hand-offs
tend to carry applause and overlap); among those, the one with the most words wins (dense speech, few pauses).
Stdlib only; runs on Windows next to ffmpeg.

usage: python pick_refs.py DIAR_JSON ASR_JSON WAV_48K OUT_DIR [--run low_1.04s] [--len 8] [--margin 5]
"""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("diar"); ap.add_argument("asr"); ap.add_argument("wav"); ap.add_argument("out")
    ap.add_argument("--run", default="low_1.04s"); ap.add_argument("--len", type=float, default=8.0)
    ap.add_argument("--margin", type=float, default=5.0)
    a = ap.parse_args()
    segs = json.loads(Path(a.diar).read_text(encoding="utf-8"))["runs"][a.run]["segments"]
    words = json.loads(Path(a.asr).read_text(encoding="utf-8"))["words"]

    turns = []  # merge consecutive segments of the same speaker
    for s, e, k in sorted(segs):
        if turns and turns[-1][2] == k and s - turns[-1][1] < 1.0:
            turns[-1][1] = max(turns[-1][1], e)
        else:
            turns.append([s, e, k])
    # other speakers' segments, to reject windows that overlap anyone else
    best = {}
    for s, e, k in turns:
        t = s + a.margin
        while t + a.len <= e - a.margin:
            clash = any(k2 != k and s2 < t + a.len and e2 > t for s2, e2, k2 in segs)
            if not clash:
                n = sum(1 for w in words if t <= w["s"] and w["e"] <= t + a.len)
                if n > best.get(k, (0,))[0]:
                    best[k] = (n, t)
            t += 0.5
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    meta = {}
    for k, (n, t) in sorted(best.items()):
        f = out / f"spk{k}.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-t", f"{a.len:.2f}", "-i", a.wav,
                        "-ac", "1", "-ar", "24000", str(f)], check=True)
        text = " ".join(w["w"] for w in words if t <= w["s"] and w["e"] <= t + a.len)
        meta[f"spk{k}"] = {"start": round(t, 2), "len": a.len, "words": n, "text": text}
        print(f"spk{k}: {t:.1f}s  {n} words  {text}")
    (out / "refs.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
