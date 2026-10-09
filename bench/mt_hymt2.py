"""M0 translation benchmark: Hy-MT2 (llama.cpp llama-server in WSL) on sentences from the Parakeet transcript.

For each model file it starts llama-server, translates the first N sentences of a segment with the official
"personalization" template (previous sentences + next sentence as context, glossary hits, a character budget
of duration x 4.5), and records per-sentence latency, budget overruns and VRAM. Translations go to a JSON for
side-by-side reading.

usage: python mt_hymt2.py ASR_JSON OUT_JSON --server LLAMA_SERVER --model-dir DIR [--n 60] [--models q4,q6,1.8b]
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

# 09-30: the official Linux build needs glibc 2.38 and WSL here is Ubuntu 22.04 (2.35), so this runs on Windows
# with the official win-cuda llama.cpp build and the GGUFs read straight from the Windows disk.
MODELS = {
    "q4": "tencent--Hy-MT2-7B-GGUF/Hy-MT2-7B-Q4_K_M.gguf",
    "q6": "tencent--Hy-MT2-7B-GGUF/HY-MT2-7B-Q6_K.gguf",
    "1.8b": "tencent--Hy-MT2-1.8B-GGUF/Hy-MT2-1.8B-Q8_0.gguf",
}
PORT = 8081
# DevDay glossary: names stay in English; product names keep the official spelling.
GLOSSARY = {
    "OpenAI": "OpenAI", "Sam": "Sam", "Tejal": "Tejal", "Romain": "Romain", "Holly": "Holly",
    "Codex": "Codex", "ChatGPT": "ChatGPT", "DevDay": "DevDay", "Dev Day": "DevDay", "Fort Mason": "梅森堡",
    "post-training": "后训练", "token": "token", "agent": "智能体", "agents": "智能体",
}
CHARS_PER_S = 4.5


def sentences(words, max_len=12.0):
    """Split word timestamps into sentences at . ? ! (or at max_len seconds)."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.?!]$", w["w"]) or cur[-1]["e"] - cur[0]["s"] >= max_len:
            out.append({"s": cur[0]["s"], "e": cur[-1]["e"], "en": " ".join(x["w"] for x in cur)})
            cur = []
    if cur:
        out.append({"s": cur[0]["s"], "e": cur[-1]["e"], "en": " ".join(x["w"] for x in cur)})
    return out


def prompt(sents, i, glossary):
    s = sents[i]
    budget = max(4, int((s["e"] - s["s"]) * CHARS_PER_S))
    prev = " ".join(x["en"] for x in sents[max(0, i - 3):i])
    nxt = sents[i + 1]["en"] if i + 1 < len(sents) else ""
    hits = [(k, v) for k, v in glossary.items() if re.search(rf"\b{re.escape(k)}\b", s["en"], re.I)]
    tasks = ["这是英文发布会直播的语音识别结果，可能有识别错误，按上下文最可能的意思翻译。"]
    if prev:
        tasks.append(f"上文，仅供理解，不要翻译：{prev}")
    if nxt:
        tasks.append(f"下文，仅供理解，不要翻译：{nxt}")
    if hits:
        tasks.append("参考下面的翻译：" + "；".join(f"{k} 翻译成 {v}" for k, v in hits))
    tasks.append(f"译文用简洁口语的中文直播字幕风格，不超过 {budget} 个字，句末不加句号。")
    tasks.append("将【待翻译文本】翻译为简体中文。")
    body = "\n".join(f"{n}、{t}" for n, t in enumerate(tasks, 1))
    return f"【待翻译文本】\n{s['en']}\n\n【翻译任务】\n{body}", budget


def post(payload):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def gpu_used_mib():
    q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int(q.stdout.strip().splitlines()[0])


def zh_len(t):
    return len(re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()…—-]", "", t))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("asr"); ap.add_argument("out")
    ap.add_argument("--n", type=int, default=60); ap.add_argument("--models", default="q4,q6,1.8b")
    ap.add_argument("--server", required=True); ap.add_argument("--model-dir", required=True)
    a = ap.parse_args()
    words = json.loads(Path(a.asr).read_text(encoding="utf-8"))["words"]
    sents = sentences(words)[: a.n]

    server = Path(a.server)
    env = dict(os.environ, PATH=str(server.parent) + os.pathsep + os.environ.get("PATH", ""))

    results = {"sentences": sents, "runs": {}}
    for key in a.models.split(","):
        base = gpu_used_mib()
        model = Path(a.model_dir) / MODELS[key]
        proc = subprocess.Popen([str(server), "-m", str(model), "-ngl", "999", "-c", "4096", "-np", "1",
                                 "--flash-attn", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
                                 "--port", str(PORT), "--host", "127.0.0.1"],
                                env=env, stdout=subprocess.DEVNULL, stderr=open(Path(a.out).with_suffix(f".{key}.server.log"), "w"))
        try:
            for _ in range(120):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2); break
                except Exception:
                    time.sleep(1)
            vram = gpu_used_mib() - base
            rows = []
            for i in range(len(sents)):
                p, budget = prompt(sents, i, GLOSSARY)
                t = time.time()
                r = post({"messages": [{"role": "user", "content": p}], "temperature": 0.7, "top_p": 0.6,
                          "top_k": 20, "repeat_penalty": 1.05, "max_tokens": 200})
                dt = time.time() - t
                zh = r["choices"][0]["message"]["content"].strip()
                rows.append({"i": i, "zh": zh, "ms": round(dt * 1000), "budget": budget, "len": zh_len(zh),
                             "over": zh_len(zh) > budget, "prompt_tokens": r["usage"]["prompt_tokens"]})
            peak = gpu_used_mib() - base
        finally:
            proc.terminate(); proc.wait(timeout=30)
        ms = sorted(x["ms"] for x in rows)
        summ = {"model": model.name, "vram_after_load_mib": vram, "vram_after_run_mib": peak,
                "ms_p50": ms[len(ms) // 2], "ms_p95": ms[int(len(ms) * 0.95) - 1], "ms_max": ms[-1],
                "over_budget": sum(x["over"] for x in rows), "n": len(rows)}
        print(key, json.dumps(summ, ensure_ascii=False), flush=True)
        results["runs"][key] = {"summary": summ, "rows": rows}
    Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
