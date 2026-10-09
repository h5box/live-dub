"""What if the delay D had been smaller? From a run's timing.jsonl: every subtitle's slack and every dub's time left
shrink by (D_run - D_new); count the ones that would have missed (subtitle: slack < 0; dub: left < 1 s, since the
dubber drops a unit it cannot finish 1 s before air). Also prints the audience delay estimate D + startup lead.
usage: uv run python bench/d_whatif.py RUN_DIR [--d 30] [--try 25,20,15]
"""
import argparse
import json
import statistics as st
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("--d", type=float, default=30.0); ap.add_argument("--try", dest="tries", default="25,20,15")
    a = ap.parse_args()
    recs = [json.loads(l) for l in open(Path(a.run) / "timing.jsonl", encoding="utf-8") if l.strip()]
    subs = [r["slack_s"] for r in recs if r.get("slack_s") is not None]
    dubs = [r["left_s"] for r in recs if r.get("dub") == "ok" and r.get("left_s") is not None]
    lags = [r["lag_s"] for r in recs if r.get("lag_s") is not None]
    # lag < 0 means the unit reached the processor before the anchor mapping expected it: the startup burst puts the
    # anchor that far behind the live edge, and the audience sees D + that lead
    lead = -st.median(lags) if lags else 0.0
    print(f"{a.run}: {len(subs)} subs, {len(dubs)} dubs; startup lead ~{lead:.1f} s -> audience delay ~{a.d + lead:.0f} s at D={a.d:.0f}")
    for d in [float(x) for x in a.tries.split(",")]:
        cut = a.d - d
        ms = sum(1 for x in subs if x - cut < 0)
        md = sum(1 for x in dubs if x - cut < 1.0)
        print(f"  D={d:.0f}: subs late {ms}/{len(subs)}, dubs dropped {md}/{len(dubs)}, audience ~{d + lead:.0f} s")


if __name__ == "__main__":
    main()
