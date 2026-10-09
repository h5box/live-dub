"""Source delivery timeline from ingest's buf/arrivals.csv ("wall,chunk,pt_end" as each chunk closed): how far the
arrival of each PT wanders from a steady real-time line. Uses (wall - pt) relative to its minimum, so 0 = the most
"on time" a chunk ever was and larger = that much later than that; prints the range per two minutes of PT.
usage: uv run python bench/arrivals.py RUN_DIR [--bin 120]
"""
import argparse
import csv
import statistics as st
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("--bin", type=float, default=120.0)
    a = ap.parse_args()
    rows = [(float(r[0]), float(r[2])) for r in csv.reader(open(Path(a.run) / "buf" / "arrivals.csv")) if len(r) >= 3]
    late = [w - pt for w, pt in rows]
    base = min(late)
    by = {}
    for (w, pt), x in zip(rows, late):
        by.setdefault(int(pt // a.bin), []).append(x - base)
    meds = []
    for k in sorted(by):
        xs = by[k]
        meds.append(st.median(xs))
        print(f"pt {k * a.bin:6.0f}  n {len(xs):3d}  late med {st.median(xs):5.1f}  max {max(xs):5.1f}")
    print(f"{len(rows)} chunks; bin medians {min(meds):.1f} .. {max(meds):.1f} s; worst chunk {max(x - base for x in late):.1f} s late")


if __name__ == "__main__":
    main()
