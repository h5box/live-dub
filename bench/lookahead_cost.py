"""What would waiting for the next N units before translating cost? From a run's timing.jsonl: unit i's translation
would start when unit i+N is committed (or after max_wait s, whichever first); its subtitle slack and its dub's time
left shrink by that wait. Counts the units that would then miss (subtitle slack < 0; dub left < 1 s).
usage: uv run python bench/lookahead_cost.py RUN_DIR [--n 1,2] [--max-wait 8]
"""
import argparse
import json
import statistics as st
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("--n", default="1,2"); ap.add_argument("--max-wait", type=float, default=8.0)
    a = ap.parse_args()
    recs = [json.loads(l) for l in open(Path(a.run) / "timing.jsonl", encoding="utf-8") if l.strip()]
    subs = sorted((r for r in recs if r.get("slack_s") is not None), key=lambda r: r["pt0"])
    left = {r["pt0"]: r["left_s"] for r in recs if r.get("dub") == "ok" and r.get("left_s") is not None}
    for n in [int(x) for x in a.n.split(",")]:
        waits, miss_sub, miss_dub = [], 0, 0
        for i, r in enumerate(subs):
            nxt = subs[i + n]["committed"] if i + n < len(subs) else r["committed"] + a.max_wait
            w = min(max(0.0, nxt - r["committed"]), a.max_wait)
            waits.append(w)
            miss_sub += r["slack_s"] - w < 0
            if r["pt0"] in left:
                miss_dub += left[r["pt0"]] - w < 1.0
        ws = sorted(waits)
        print(f"lookahead {n} (max wait {a.max_wait:.0f}s): wait med {st.median(ws):.1f} p90 {ws[int(len(ws)*.9)]:.1f} s; "
              f"subs late {miss_sub}/{len(subs)}, dubs dropped {miss_dub}/{len(left)}")


if __name__ == "__main__":
    main()
