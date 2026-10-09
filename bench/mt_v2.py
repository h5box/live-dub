"""Translation v2 (after the user's 09-30 review): merge fragments, feed back what was already broadcast,
looser length budget, fuzzy glossary. Hy-MT2-7B Q4 on Windows llama.cpp, same template family as mt_hymt2.py.

Changes vs v1 (mt_hymt2.py):
  - units: ASR sentences shorter than 1.5 s, under 4 words, or without terminal punctuation are glued to the next
    one of the same speaker (cap 12 s; gluing across a hand-off put Sam's and Tejal's lines in one dub); each unit keeps src_ids of the ASR sentences it covers;
  - context: previous 3 units as "English -> broadcast Chinese" pairs (already on air, keep names/terms consistent),
    next unit as plain English;
  - budget: max(8, duration x 5.5) characters (4.5/s squeezed #26 into a mistranslation and left 24 dubs short);
  - glossary: capitalised ASR tokens within edit distance 1-2 of a name are corrected in the English first
    (Tajal/Tajil -> Tejal), then hits are injected; adds technical terms (harness).
Output keeps the v1 shape ({"sentences": units, "runs": {"q4": {"rows": ...}}}) so tts/compose scripts work unchanged.

usage: python mt_v2.py ASR_JSON OUT_JSON --diar DIAR_JSON --server LLAMA_SERVER --model GGUF [--n-src 60]
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import livedub.mt as mtlib  # noqa: E402
from livedub.mt import PORT, units_from, prompt, copies_context, zh_len, ask_llama  # noqa: E402,F401

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asr"); ap.add_argument("out")
    ap.add_argument("--server", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--n-src", type=int, default=60); ap.add_argument("--diar", required=True)
    ap.add_argument("--target-cps", type=float, default=5.0, help="0 = v2 behaviour (upper bound only)")
    a = ap.parse_args()
    mtlib.TARGET_CPS = a.target_cps
    words = json.loads(Path(a.asr).read_text(encoding="utf-8"))["words"]
    segs = json.loads(Path(a.diar).read_text(encoding="utf-8"))["runs"]["low_1.04s"]["segments"]
    units, n_src = units_from(words, a.n_src, segs)

    proc = subprocess.Popen([a.server, "-m", a.model, "-ngl", "999", "-c", "4096", "-np", "1", "--flash-attn", "on",
                             "--cache-type-k", "q8_0", "--cache-type-v", "q8_0", "--port", str(PORT), "--host", "127.0.0.1"],
                            stdout=subprocess.DEVNULL, stderr=open(Path(a.out).with_suffix(".server.log"), "w"))
    rows, zh = [], []
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2); break
            except Exception:
                time.sleep(1)
        for i in range(len(units)):
            p, budget = prompt(units, zh, i)
            t = time.time()
            out, retried = ask_llama(p), False
            if copies_context(out, zh[-3:]):
                out, retried = ask_llama(prompt(units, zh, i, with_prev_zh=False)[0]), True
            zh.append(out)
            rows.append({"i": i, "zh": out, "ms": round((time.time() - t) * 1000), "budget": budget,
                         "len": zh_len(out), "over": zh_len(out) > budget, "retried_copy": retried})
    finally:
        proc.terminate(); proc.wait(timeout=30)
    ms = sorted(r["ms"] for r in rows)
    summ = {"units": len(units), "from_sentences": n_src, "merged_units": sum(len(u["src_ids"]) > 1 for u in units),
            "ms_p50": ms[len(ms) // 2], "ms_p95": ms[int(len(ms) * 0.95) - 1], "over_budget": sum(r["over"] for r in rows),
            "empty": sum(not r["zh"] for r in rows), "retried_copy": sum(r["retried_copy"] for r in rows)}
    print(json.dumps(summ, ensure_ascii=False))
    Path(a.out).write_text(json.dumps({"version": 2, "summary": summ, "sentences": units, "runs": {"q4": {"rows": rows}}},
                                      ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
