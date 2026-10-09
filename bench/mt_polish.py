"""Second-pass polish experiment: give a model the English line, a few lines of English before it, and a first
translation, and ask for a corrected, natural Chinese subtitle. Reads a mt_replay table (column COL is the first
translation), writes the chosen rows with the polished line next to it, and prints the time per line.

usage: uv run python bench/mt_polish.py TABLE.tsv COL OUT.tsv --port 8082 [--rows 8,11,...] [--random 30] [--style qwen|hymt]
"""
import argparse
import json
import random
import re
import time
import urllib.request

PROMPT = """你是中文直播字幕的译审。下面是英文直播的一句话（语音识别结果，可能有识别错误）和它的机器初译。
请在忠于原意的前提下修改初译：改正误译、漏译和不通顺的地方，用自然的中文口语，人名、机构名保持初译的写法。
初译已经正确通顺的就原样输出。只输出最终译文，不要解释，不要加引号。

前文（仅供理解）：{prev}
原文：{en}
初译：{zh}
最终译文："""

PROMPT_PAIRS = """你是中文直播字幕的译审。下面是英文直播的一句话（语音识别结果，可能有识别错误）和它的机器初译，
前面几句的原文和已播出的译文也给你参考（人名、术语要与之一致）。
请在忠于原意的前提下修改初译：改正误译、漏译和不通顺的地方，用自然的中文口语，人名、机构名保持初译的写法。
初译已经正确通顺的就原样输出。前文只用来理解，不要把前文里的信息加进这一句，原文没有的内容一个字也不要加；
原文只是半句或几个词时，也只译这几个词。只输出最终译文，不要解释，不要加引号。

前文（原文 → 译文，仅供理解）：
{pairs}
原文：{en}
初译：{zh}
最终译文："""

PROMPT_DIRECT = """把英文直播的一句话翻译成中文直播字幕。这句是语音识别结果，可能有识别错误，按上下文最可能的意思翻译。
用自然简洁的中文口语，人名按通行中文译名，只输出译文，不要解释，不要加引号。

前文（仅供理解，不要翻译）：{prev}
要翻译的句子：{en}
译文："""

PROMPT_HYMT = """【待润色译文】
{zh}

【翻译任务】
1、这是英文新闻直播一句话的机器初译，原文是：{en}
2、上文，仅供理解：{prev}
3、对照原文改正误译和不通顺的地方，用自然的中文口语字幕风格；已经正确通顺的就原样输出。
4、只输出修改后的简体中文译文。"""


def ask(port, text, temperature=0.3, max_tokens=200):
    # enable_thinking off: hybrid reasoning models (MiniCPM5) otherwise spend the tokens thinking
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(
        {"messages": [{"role": "user", "content": text}], "temperature": temperature, "top_p": 0.8,
         "max_tokens": max_tokens,
         "chat_template_kwargs": {"enable_thinking": False}}).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip().strip('"“”')


def guard(first, pol):
    """Keep the first translation when the polish looks broken: much longer or shorter (a small model given a
    one-word fragment translated the whole context instead), or a name from the first translation is gone."""
    n1, n2 = len(first), len(pol)
    if n2 > 1.6 * n1 + 6 or n2 < 0.5 * n1:
        return first, "len"
    # names: the two characters on each side of the middle dot (安迪·伯纳姆 -> 安迪, 伯纳); a regex over the whole run
    # of Chinese characters took "听安迪" as the name and flagged a correct polish
    for m in re.finditer("·", first):
        for key in (first[max(0, m.start() - 2):m.start()], first[m.end():m.end() + 2]):
            if len(key) == 2 and key not in pol:
                return first, "name"
    return pol, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("table"); ap.add_argument("col"); ap.add_argument("out")
    ap.add_argument("--port", type=int, default=8082); ap.add_argument("--style", default="qwen")
    ap.add_argument("--rows", default=""); ap.add_argument("--random", type=int, default=30)
    ap.add_argument("--prev", type=int, default=8)
    a = ap.parse_args()
    lines = [l.rstrip("\n").split("\t") for l in open(a.table, encoding="utf-8")]
    head, rows = lines[0], lines[1:]
    ci = head.index(a.col)
    pick = [int(x) for x in a.rows.split(",") if x.strip()]
    rest = [i for i in range(1, len(rows) + 1) if i not in pick]
    random.Random(0).shuffle(rest)
    pick = sorted(set(pick) | set(rest[: a.random]))
    tmpl = {"qwen": PROMPT, "hymt": PROMPT_HYMT, "pairs": PROMPT_PAIRS, "direct": PROMPT_DIRECT}[a.style]
    out, times = [], []
    for n in pick:
        r = rows[n - 1]
        prev = " ".join(rows[j][3] for j in range(max(0, n - 1 - a.prev), n - 1))
        pairs = "\n".join(f"{rows[j][3]} → {rows[j][ci]}" for j in range(max(0, n - 1 - a.prev), n - 1)) or "（无）"
        t = time.time()
        raw = ask(a.port, tmpl.format(prev=prev or "（无）", pairs=pairs, en=r[3], zh=r[ci]))
        times.append(time.time() - t)
        pol, why = guard(r[ci], raw)
        out.append([r[0], r[3], r[ci], pol, "same" if pol.strip("。") == r[ci].strip("。") else "", why, raw if why else ""])
        print(f"{n}\t{times[-1]:.2f}s\t{pol}", flush=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write("n\ten\tfirst\tpolished\tsame\tguard\traw\n")
        for o in out:
            f.write("\t".join(o) + "\n")
    ts = sorted(times)
    print(f"{len(out)} lines; unchanged {sum(1 for o in out if o[4])}; guarded {sum(1 for o in out if o[5])}; time med {ts[len(ts)//2]:.2f}s max {ts[-1]:.2f}s")


if __name__ == "__main__":
    main()
