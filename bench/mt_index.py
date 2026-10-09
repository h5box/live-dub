"""Replay a live run's English units through an Index-Translate model (bilibili, Qwen3.5-based) served by
llama-server, the way the processor would: previous N units' English as context, glossary as terminology constraints,
greedy decoding (the model card's recommendation). Adds a column to an existing mt_replay table for side-by-side
judging.

usage: uv run python bench/mt_index.py RUN_DIR CONFIG TABLE.tsv NAME --port 8083 [--prev 8]
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livedub import mt  # noqa: E402


def ask(port, text):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(
        {"messages": [{"role": "user", "content": text}], "temperature": 0, "max_tokens": 200,
         "chat_template_kwargs": {"enable_thinking": False}}).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.load(r)["choices"][0]["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()


def prompt(units, i, n_prev, domain, glossary, variant="a", speakers=False):
    u = units[i]
    budget = max(mt.MIN_BUDGET, int((u["e"] - u["s"]) * mt.CPS))
    parts = [f"这是{domain}的语音识别结果，可能有识别错误，按上下文最可能的意思翻译。"]
    if speakers:  # one line per unit, tagged with the diarized speaker, and the line to translate tagged too
        prev = "\n".join(f"[说话人{units[j]['spk']}] {units[j]['en']}" for j in range(max(0, i - n_prev), i))
        if prev:
            parts.append(f"上文（按说话人标注，仅供理解，不要翻译）：\n{prev}")
        parts.append(f"要翻译的这句是说话人{u['spk']}说的。")
    else:
        prev = " ".join(units[j]["en"] for j in range(max(0, i - n_prev), i))
        if prev:
            parts.append(f"上文（仅供理解，不要翻译）：{prev}")
    hits = [(k, v) for k, v in glossary.items() if re.search(rf"\b{re.escape(k)}\b", u["en"], re.I)][:10]
    if hits:
        parts.append("术语：" + "；".join(f"{k} 译为 {v}" for k, v in hits))
    if variant == "a":
        parts.append(f"用简洁口语的中文直播字幕风格，不超过 {budget} 个字，句末不加句号，只输出译文。")
        return "\n".join(parts) + f"\n\n将以下文本翻译为简体中文：\n\n{u['en']}"
    if variant == "b":
        parts.append("用简洁口语的中文直播字幕风格，句末不加句号，只输出译文。")
        return "\n".join(parts) + f"\n\n将以下文本翻译为简体中文，译文不超过 {budget} 个汉字：\n\n{u['en']}"
    # c: say why (the dub has to fit the speaker's time) and what may be dropped
    parts.append("这是同声传译的配音稿，要在原话的时长内念完：用简洁的中文口语，可以省掉语气词、口头禅和重复，"
                 "意思不能丢，句末不加句号，只输出译文。")
    return "\n".join(parts) + f"\n\n将以下文本翻译为简体中文，译文不超过 {budget} 个汉字：\n\n{u['en']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run"); ap.add_argument("config"); ap.add_argument("table"); ap.add_argument("name")
    ap.add_argument("--port", type=int, default=8083); ap.add_argument("--prev", type=int, default=8)
    ap.add_argument("--variant", default="a")
    ap.add_argument("--speakers", action="store_true", help="tag context lines with the diarized speaker")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    ev = {}
    for line in open(Path(a.run) / "events.jsonl", encoding="utf-8"):
        if line.strip():
            e = json.loads(line); ev[e["pt0"]] = e
    units = [{"s": e["pt0"], "e": e["pt1"], "en": e.get("en") or "", "spk": e.get("spk", 0)} for e in (ev[k] for k in sorted(ev))]
    lines = [l.rstrip("\n").split("\t") for l in open(a.table, encoding="utf-8")]
    assert len(lines) - 1 == len(units), "table and run differ"
    outs, ms, over, cps = [], [], 0, []
    for i, u in enumerate(units):
        t = time.time()
        out = ask(a.port, prompt(units, i, a.prev, cfg.get("domain", "英文直播"), cfg.get("glossary", {}), a.variant, a.speakers))
        ms.append((time.time() - t) * 1000)
        over += mt.zh_len(out) > max(mt.MIN_BUDGET, int((u["e"] - u["s"]) * mt.CPS))
        outs.append(out.replace("\t", " ").replace("\n", " "))
        cps.append(mt.zh_len(out) / max(0.5, u["e"] - u["s"]))
    lines[0].append(a.name)
    for row, o in zip(lines[1:], outs):
        row.append(o)
    Path(a.table).write_text("\n".join("\t".join(r) for r in lines) + "\n", encoding="utf-8")
    ms.sort(); cps.sort()
    print(f"{a.name}: {len(units)} units, ms med {ms[len(ms)//2]:.0f} p95 {ms[int(len(ms)*.95)]:.0f}, over budget {over}, "
          f"chars/s med {cps[len(cps)//2]:.2f} p90 {cps[int(len(cps)*.9)]:.2f}, >6.5 {sum(c > 6.5 for c in cps)}")


if __name__ == "__main__":
    main()
