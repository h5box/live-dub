"""Replay a live run's units, in order, through livedub.mt_chat (one append-only conversation, as the processor uses it)
and compare with the translations that aired. Look-ahead = the next unit's English (live: the uncommitted tail).
Prices are deepseek-flash's (CNY per million tokens, 10-04): cache hit 0.04 / miss 2 / out 8 at peak, half off-peak.

usage: python bench/mt_replay_chat.py RUN_DIR CONFIG --tag NAME [--limit N] [--compact-at TOKENS] [--temperature T]
       [--target-cps 5.0] [--thinking off|low|high|max] [--prompt v5]
writes outputs/mt_chat/<tag>/rows.jsonl, side.tsv (aired vs new, for judging), calls.jsonl, summary.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from livedub import mt  # noqa: E402
from livedub.mt_chat import ChatMT  # noqa: E402

PEAK = {"hit": 0.04, "miss": 2.0, "out": 8.0}


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else None


ap = argparse.ArgumentParser()
ap.add_argument("run"); ap.add_argument("config")
ap.add_argument("--tag", required=True)
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--compact-at", type=int, default=None)
ap.add_argument("--temperature", type=float, default=1.3)
ap.add_argument("--target-cps", type=float, default=5.0, help="aim = duration x this (config mt_target_cps); 0: no aim")
ap.add_argument("--thinking", default="off", help="off | low | high | max (config mt_api_thinking)")
ap.add_argument("--prompt", default="v5", help="mt_chat.PROMPTS version (config mt_api_prompt)")
a = ap.parse_args()
cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
first = {}
for ln in (Path(a.run) / "events.jsonl").read_text(encoding="utf-8").splitlines():
    if ln.strip():
        e = json.loads(ln)
        first.setdefault(e["pt0"], e)
evs = sorted(first.values(), key=lambda e: e["pt0"])
if a.limit:
    evs = evs[: a.limit]
out = ROOT / "outputs/mt_chat" / a.tag
out.mkdir(parents=True, exist_ok=True)
calls = out / "calls.jsonl"
calls.unlink(missing_ok=True)
chat = ChatMT("deepseek-flash", "deepseek", domain=cfg.get("domain", mt.DOMAIN), names=cfg.get("names", []),
              glossary=cfg.get("glossary"), temperature=a.temperature, compact_at=a.compact_at, log_path=calls,
              thinking=a.thinking, prompt=a.prompt)
units = [{"s": e["pt0"], "e": e["pt1"], "en": e["en"]} for e in evs]
zh, rows = [], []
t_all = time.time()
with open(out / "rows.jsonl", "w", encoding="utf-8") as fr, open(out / "side.tsv", "w", encoding="utf-8") as fs:
    fs.write("i\tpt0\ten\taired\tdeepseek\n")
    for i, (u, e) in enumerate(zip(units, evs)):
        budget = max(mt.MIN_BUDGET, int((u["e"] - u["s"]) * mt.CPS))
        tail = units[i + 1]["en"] if i + 1 < len(units) else ""
        t = time.time()
        aim = max(4, int((u["e"] - u["s"]) * a.target_cps)) if a.target_cps else None
        res, rec = chat.translate(u["en"], tail, budget, aim, dur=u["e"] - u["s"])
        first = res
        steps = ["mt"]
        seq = units[: i + 1]
        if mt.echoed(res, u["en"], seq, zh + [""], i, latin=False):
            res, _ = chat.redo_no_repeat(); steps.append("echo")
        if mt.zh_len(res) > budget + chat.slack:
            new, _ = chat.shorten(budget, mt.zh_len(res)); steps.append("shorten")
            if new and mt.zh_len(new) < mt.zh_len(res):
                res = new
        wall = time.time() - t
        zh.append(res)
        row = {"i": i, "pt0": u["s"], "dur": round(u["e"] - u["s"], 2), "budget": budget, "en": u["en"],
               "aired": e["zh"], "zh": res, "wall_s": round(wall, 3), "first_s": rec["wall_s"], "steps": steps,
               "over": mt.zh_len(res) > budget, "first": first, "prompt": rec["prompt"], "hit": rec["hit"]}
        rows.append(row)
        fr.write(json.dumps(row, ensure_ascii=False) + "\n"); fr.flush()
        fs.write(f"{i}\t{u['s']:.1f}\t{u['en']}\t{e['zh']}\t{res}\n")
        if i % 25 == 0:
            print(f"{i}/{len(units)} wall {wall:.2f}s prompt {rec['prompt']} hit {rec['hit']} | {res}", flush=True)
time.sleep(1)
cs = [json.loads(ln) for ln in calls.read_text(encoding="utf-8").splitlines() if ln.strip()]
api = [c for c in cs if "prompt" in c]
tok = {k: sum(c.get(k) or 0 for c in api) for k in ("prompt", "hit", "miss", "out", "reasoning")}
cost = (tok["hit"] * PEAK["hit"] + tok["miss"] * PEAK["miss"] + tok["out"] * PEAK["out"]) / 1e6
hours = (units[-1]["e"] - units[0]["s"]) / 3600
summ = {"tag": a.tag, "prompt": a.prompt, "thinking": a.thinking, "compact_at": chat.compact_at, "units": len(rows), "stream_h": round(hours, 2), "replay_s": round(time.time() - t_all, 1),
        "wall_p50": pct([r["wall_s"] for r in rows], .5), "wall_p90": pct([r["wall_s"] for r in rows], .9),
        "wall_p99": pct([r["wall_s"] for r in rows], .99), "wall_max": max(r["wall_s"] for r in rows),
        "first_call_p50": pct([r["first_s"] for r in rows], .5),
        "echo": sum("echo" in r["steps"] for r in rows), "shorten": sum("shorten" in r["steps"] for r in rows),
        "over_budget": sum(r["over"] for r in rows), "aired_over_budget": sum(mt.zh_len(r["aired"]) > r["budget"] for r in rows),
        "zh_chars": sum(mt.zh_len(r["zh"]) for r in rows), "aired_chars": sum(mt.zh_len(r["aired"]) for r in rows),
        "calls": len(api), "tokens": tok, "hit_ratio": round(tok["hit"] / max(1, tok["prompt"]), 4),
        "last_prompt": api[-1]["prompt"] if api else None, "compactions": chat.compactions,
        "cost_peak_cny": round(cost, 3), "cost_peak_per_h": round(cost / max(hours, 1e-6), 3),
        "errors": sum("error" in c for c in cs)}
print("SUMMARY", json.dumps(summ, ensure_ascii=False), flush=True)
(out / "summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
