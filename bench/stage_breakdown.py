"""Where a sentence's time goes, node by node, for a live run dir that has buf/arrivals.csv (`run_live.ps1 -Keep` era).

usage: python bench/stage_breakdown.py outputs/runs/live9b [outputs/runs/live6 ...]

Times come from the wall clock at which each 1 s chunk landed (arrivals.csv), not the playout anchor, so a startup lead
(streamlink's first burst) doesn't bias them. Per unit:
  A speech     pt0 -> pt1: waiting for the speaker to finish
  B ingest     source end of speech -> its chunk on disk, relative to the most punctual chunk of the run
               (YouTube's 5 s HLS segments + delivery jitter through the proxy)
  C asr        chunk on disk -> sentence committed: 2 s step, 1 s tail guard, ASR window + speaker embedding
  D hold       committed -> MT start: fragments held for the same speaker's next sentence (<= 4 s)
  E mt         translation incl. echo retry / shorten
  F1 queue     event written -> TTS request sent (deadline-ordered queue)
  F2 tts       TTS request -> wav back
"""
import bisect
import json
import sys
from pathlib import Path


def pct(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    f = int(k); c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def row(name, xs):
    print(f"  {name:28s} med {pct(xs, 50):6.2f}  p90 {pct(xs, 90):6.2f}  p95 {pct(xs, 95):6.2f}  max {max(xs):6.2f}  "
          f"mean {sum(xs) / len(xs):6.2f}")


for d in map(Path, sys.argv[1:]):
    anchor = json.loads((d / "anchor.json").read_text(encoding="utf-8"))
    T0, p0, D = anchor["T0"], anchor["p0"], anchor["delay"]
    arr = sorted((float(pe), float(w)) for w, _, pe in
                 (ln.split(",") for ln in (d / "buf" / "arrivals.csv").read_text(encoding="utf-8").splitlines()))
    ends, walls = [a[0] for a in arr], [a[1] for a in arr]
    ref = min(w - pe for pe, w in arr)

    def landed(pt):
        return walls[min(bisect.bisect_left(ends, pt), len(walls) - 1)]

    recs = [json.loads(ln) for ln in (d / "timing.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    units = {r["pt0"]: r for r in recs if "pt1" in r}
    dubs = {r["pt0"]: r for r in recs if r.get("dub") == "ok" and r["pt0"] in units}
    parts = {k: [] for k in ("A speech", "B ingest", "C asr", "D hold", "E mt", "F1 queue", "F2 tts")}
    sub_ready, dub_ready = [], []
    for u in units.values():
        parts["A speech"].append(u["pt1"] - u["pt0"])
        parts["B ingest"].append(landed(u["pt1"]) - (ref + u["pt1"]))
        parts["C asr"].append(u["committed"] - landed(u["pt1"]))
        parts["D hold"].append(u["written"] - u["committed"] - u["mt_s"])
        parts["E mt"].append(u["mt_s"])
        sub_ready.append(u["written"] - (ref + u["pt0"]))
    for p, r in dubs.items():
        t_end = T0 + D + (p - p0) - 1.0 - r["left_s"]       # the processor's deadline is air time - 1 s
        parts["F1 queue"].append(t_end - r["tts_s"] - units[p]["written"])
        parts["F2 tts"].append(r["tts_s"])
        dub_ready.append(t_end - (ref + p))
    print(f"== {d.name}: {len(units)} units, {len(dubs)} dubbed")
    for k, xs in parts.items():
        row(k, xs)
    row("start of speech -> subtitle", sub_ready)
    row("start of speech -> dub", dub_ready)
    means = {k: sum(xs) / len(xs) for k, xs in parts.items()}
    tot = sum(means.values())
    print("  mean share: " + ", ".join(f"{k} {v:.2f}s {v / tot:.0%}" for k, v in means.items()))
