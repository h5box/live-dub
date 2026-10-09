"""Watch a live HLS variant alongside a test: every few seconds log the playlist's MEDIA-SEQUENCE, how long the
playlist and its newest segment take to fetch, and any errors, so dropped segments in ingest can be told apart:
a sequence jump means the source skipped; a fetch-time spike means our download path stalled.
Light on bandwidth so it doesn't compete with ingest: the playlist every few seconds, the newest segment's first
64 KB (time to first bytes) each time, and a whole segment only every --full seconds.
usage: uv run python bench/hls_watch.py CONFIG OUT.jsonl [--secs 3600] [--every 5]
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livedub.ingest import resolve  # noqa: E402


def fetch(u, head=None):
    t = time.time()
    req = urllib.request.Request(u, headers={"Range": f"bytes=0-{head - 1}"} if head else {})
    b = urllib.request.urlopen(req, timeout=30).read()
    return b, round(time.time() - t, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config"); ap.add_argument("out")
    ap.add_argument("--secs", type=float, default=3600); ap.add_argument("--every", type=float, default=5)
    ap.add_argument("--full", type=float, default=60)
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    url, t_end, last_seq, last_full = None, time.time() + a.secs, None, 0.0
    with open(a.out, "a", encoding="utf-8") as f:
        while time.time() < t_end:
            row = {"t": time.strftime("%H:%M:%S")}
            try:
                if url is None:
                    url = resolve(cfg["src"], cfg.get("cookies"))
                pl, row["pl_s"] = fetch(url)
                lines = pl.decode().splitlines()
                seq = next(int(l.split(":")[1]) for l in lines if l.startswith("#EXT-X-MEDIA-SEQUENCE"))
                segs = [l for l in lines if l and not l.startswith("#")]
                row.update(seq=seq, n=len(segs), jump=None if last_seq is None else seq - last_seq)
                last_seq = seq
                seg = segs[-1] if segs[-1].startswith("http") else url.rsplit("/", 1)[0] + "/" + segs[-1]
                _, row["head_s"] = fetch(seg, 65536)
                if time.time() - last_full >= a.full:
                    b, row["seg_s"] = fetch(seg)
                    row["seg_mb"] = round(len(b) / 1e6, 2)
                    last_full = time.time()
            except Exception as ex:
                row["err"] = f"{type(ex).__name__}: {str(ex)[:120]}"
                if "403" in str(ex) or "410" in str(ex):
                    url = None  # signed URL expired: resolve again
            f.write(json.dumps(row) + "\n"); f.flush()
            time.sleep(a.every)


if __name__ == "__main__":
    main()
