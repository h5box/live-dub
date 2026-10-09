"""Replay a live run's English units through the translator under a few prompt / sampling variants and write a
side-by-side table, so translation changes are judged on real stream text instead of by feel.

Each arm translates the whole sequence in order with its own previous outputs as context, the way the processor does
(下文 = the next unit's English; live it was the uncommitted tail). Needs llama-server on :8081 and nothing else live on
it (a running test's MT latency would be skewed).

usage: uv run python bench/mt_replay.py RUN_DIR CONFIG OUT_PREFIX [--arms v0,v1,v2] [--judge 8,11,27 --random 30]
writes OUT_PREFIX.tsv (all units), OUT_PREFIX_judge.tsv (the judge set), and prints per-arm counts.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livedub import mt  # noqa: E402

ARMS = {
    # name: (domain from config?, strict echo rule?, temperature, previous translations in the context?, units of context)
    "v0": (False, False, 0.7, True, 3),   # as live on 09-30 night
    "v1": (True, True, 0.7, True, 3),
    "v2": (True, True, 0.3, True, 3),     # live since 09-30 11:45
    "v3": (True, True, 0.3, False, 3),    # English-only context
    "v4": (True, True, 0.3, False, 8),    # longer English-only context
    "v5": (True, True, 0.3, False, 16),
    "v6": (True, True, 0.3, False, 32),
    # look-ahead variants of v4: how many following units are given as 下文 (the arms above all use 1)
    "la0": (True, True, 0.3, False, 8, 0),   # none: closest to live today (live only has the uncommitted tail)
    "la2": (True, True, 0.3, False, 8, 2),
}


def run_arm(units, name, domain):
    use_domain, strict, temp, with_zh, n_prev, *rest = ARMS[name]
    ahead = rest[0] if rest else 1
    mt.DOMAIN = domain if use_domain else "英文发布会直播"
    zh, stats = [], {"echo_final": 0, "retries": 0, "empty": 0, "over": 0, "ms": 0.0}
    for i, u in enumerate(units):
        seq = units[: i + 1 + ahead]
        ctx = zh + [""] * (len(seq) - len(zh))
        t = time.time()
        text, budget = mt.prompt(seq, ctx, i, with_prev_zh=with_zh, n_prev=n_prev)
        out = mt.ask_llama(text, temperature=temp)
        if strict:
            hits = mt.echoed(out, u["en"], seq, ctx, i)
            if hits:
                stats["retries"] += 1
                out = mt.ask_llama(mt.prompt(seq, ctx, i, with_prev_zh=with_zh, drop=set(hits), no_repeat=True,
                                             n_prev=n_prev)[0], temperature=temp)
        elif mt.copies_context(out, zh[-3:]):
            stats["retries"] += 1
            out = mt.ask_llama(mt.prompt(seq, ctx, i, with_prev_zh=False, n_prev=n_prev)[0], temperature=temp)
        stats["ms"] += (time.time() - t) * 1000
        # scored with the strict rule for every arm, so arms are comparable
        if mt.echoed(out, u["en"], seq, ctx, i):
            stats["echo_final"] += 1
        stats["empty"] += not out.strip()
        stats["over"] += mt.zh_len(out) > budget
        zh.append(out)
    stats["ms"] = round(stats["ms"] / max(1, len(units)))
    return zh, stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("config"); ap.add_argument("out")
    ap.add_argument("--arms", default="v0,v1,v2")
    ap.add_argument("--judge", default=""); ap.add_argument("--random", type=int, default=30)
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    mt.TARGET_CPS = float(cfg.get("mt_target_cps", 5.0))
    mt.GLOSSARY = cfg.get("glossary", mt.GLOSSARY)
    mt.NAMES = cfg.get("names", mt.NAMES)
    domain = cfg.get("domain", "英文发布会直播")
    ev = {}
    for line in open(Path(a.run) / "events.jsonl", encoding="utf-8"):
        if line.strip():
            e = json.loads(line); ev[e["pt0"]] = e
    units = [{"s": e["pt0"], "e": e["pt1"], "en": e.get("en") or "", "live": e.get("zh") or ""}
             for e in (ev[k] for k in sorted(ev))]
    arms = a.arms.split(",")
    res = {}
    for name in arms:
        zh, stats = run_arm(units, name, domain)
        res[name] = zh
        print(name, ARMS[name], stats, flush=True)
    cols = ["n", "pt0", "dur", "en", "live"] + arms
    rows = [[str(i + 1), f"{u['s']:.1f}", f"{u['e'] - u['s']:.1f}", u["en"], u["live"]] + [res[n][i] for n in arms]
            for i, u in enumerate(units)]
    Path(a.out + ".tsv").write_text("\n".join("\t".join(r) for r in [cols] + rows) + "\n", encoding="utf-8")
    pick = [int(x) for x in a.judge.split(",") if x.strip()]
    rest = [i for i in range(1, len(units) + 1) if i not in pick]
    random.Random(0).shuffle(rest)
    pick = sorted(set(pick) | set(rest[: a.random]))
    Path(a.out + "_judge.tsv").write_text("\n".join("\t".join(r) for r in [cols] + [rows[i - 1] for i in pick]) + "\n",
                                          encoding="utf-8")
    print("wrote", a.out + ".tsv", len(rows), "rows;", a.out + "_judge.tsv", len(pick), "rows")


if __name__ == "__main__":
    main()
