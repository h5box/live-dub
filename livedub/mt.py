"""Translation helpers shared by the processor and the bench scripts: the model table (local GGUFs for llama-server,
online models used through mt_chat.py), the prompts for the local models (Hy-MT2, Index-Translate) with context,
glossary and length target; fragment merging into same-speaker units; ASR name fixing; copied-context detection.
Why each rule exists: docs/dev/实测-M0.md, docs/dev/实测-M2.md.
"""
import json
import re
import urllib.request

from . import settings

# Translation models; a preset's "mt_model" (or settings mt.default) picks one. Local ones are GGUF files served by
# llama-server: livedub/install.py downloads "file" from Hugging Face "hf" or ModelScope "ms" into the download folder
# (settings models_dir; mt.models.<key>.path points elsewhere) and checks it against "bytes" and "sha256". Online ones
# go through mt_chat.py with the provider's key in .secret/.
MODELS = {
    # bilibili Index-Translate-9B (Qwen3.5 base, Apache-2.0); replayed on a news channel it beat Hy-MT2-7B on ~17 of
    # 54 judged lines and lost ~7, but runs long -> shorten() below
    "index-9b": {"file": "Index-Translate-9B.IQ4_XS.gguf", "hf": "IndexTeam/Index-Translate-9B-GGUF",
                 "ms": "IndexTeam/Index-Translate-9B-GGUF", "bytes": 5357874624,
                 "sha256": "874e47a25f38c236be8ac5bec03aa9d47432a31036b60a00c8ca381750aaa619",
                 "style": "index", "label": "Index-Translate-9B", "vram_gb": 5.0},
    "hy-mt2": {"file": "Hy-MT2-7B-Q4_K_M.gguf", "hf": "tencent/Hy-MT2-7B-GGUF", "ms": "Tencent-Hunyuan/Hy-MT2-7B-GGUF",
               "bytes": 4624648896, "sha256": "9f96256500f3fc1ab4d64336b58f52a949a95ad7516b0c229476eef782f9f77b",
               "style": "hymt", "label": "Hy-MT2-7B", "vram_gb": 4.9},
    # online: one append-only conversation per run (the whole stream as context, prefix-cached)
    "deepseek-flash": {"api": "deepseek", "model": "deepseek-flash", "ctx": 1_000_000, "style": "chat",
                       "label": "DeepSeek"},
    "custom": {"api": "custom", "model": "", "ctx": 128_000, "style": "chat", "label": "自定义接口"},
}
STYLE = "hymt"
CPS, MIN_BUDGET, MAX_UNIT = 5.5, 8, 12.0
DOMAIN = "外语直播"  # per stream (preset "domain"), e.g. 英文新闻直播
PREV_UNITS = 3  # how many earlier units go into the prompt as context
TEMPERATURE = 0.7  # Hunyuan-MT's recommended sampling (with top_p 0.6, top_k 20, repeat_penalty 1.05)
TARGET_CPS = 0.0  # >0: also ask for "about N characters" (N = duration x TARGET_CPS), to fight dubs that end early
NAMES: list[str] = []  # per stream (preset "names"): names ASR may misspell, fixed by edit distance
GLOSSARY: dict[str, str] = {}  # per stream (preset "glossary"): term -> translation


def models() -> dict:
    """MODELS with settings mt.models merged in (per key)."""
    out = {k: dict(v) for k, v in MODELS.items()}
    for k, v in settings.get()["mt"].get("models", {}).items():
        out[k] = {**out.get(k, {}), **v}
    return out


def gguf_path(key: str) -> str | None:
    m = models().get(key) or {}
    if m.get("api"):
        return None
    return str(settings.path(m["path"]) if m.get("path") else settings.path(settings.get()["models_dir"]) / m["file"])


def llama_argv(key: str) -> list[str]:
    """llama-server for a local model, on 127.0.0.1:<ports.mt>."""
    s = settings.get()["mt"]
    return [settings.tool("llama_server") or "llama-server", "-m", gguf_path(key), "-ngl", str(s["gpu_layers"]),
            "-c", str(s["ctx"]), "-np", "1", "--flash-attn", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "--port", str(settings.port("mt")), "--host", "127.0.0.1"]


def edit(a, b):
    a, b = a.lower(), b.lower()
    d = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, d[0] = d[0], i
        for j, cb in enumerate(b, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (ca != cb))
    return d[-1]


def fix_names(text):
    def sub(m):
        w = m.group(0)
        # a capital at the start of a sentence says nothing about a name ("Same thing." is not "Sam", "Seven people"
        # not "Kevin"), so there only a case-only difference is fixed
        before = m.string[:m.start()].rstrip()
        first = not before or before[-1] in ".?!"
        for n in NAMES:
            lim = 0 if first else 1 if len(n) < 5 else 2
            if len(w) >= 4 and w != n and edit(w, n) <= lim and w[0].isupper():
                return n
        return w
    return re.sub(r"[A-Za-z][A-Za-z\-]+", sub, text)


def speaker_of(s, e, segs):
    ov = {}
    for s2, e2, k in segs:
        o = min(e, e2) - max(s, s2)
        if o > 0:
            ov[k] = ov.get(k, 0) + o
    return max(ov, key=ov.get) if ov else None


def units_from(words, n_src, segs):
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if re.search(r"[.?!]$", w["w"]) or cur[-1]["e"] - cur[0]["s"] >= MAX_UNIT:
            sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    sents = sents[:n_src]
    units, buf, ids = [], [], []
    for k, s in enumerate(sents):
        buf += s; ids.append(k)
        dur = buf[-1]["e"] - buf[0]["s"]
        frag = (s[-1]["e"] - s[0]["s"] < 1.5 or len(s) < 4 or not re.search(r"[.?!]$", s[-1]["w"]))
        nxt = sents[k + 1] if k + 1 < len(sents) else None
        # never glue across a speaker change: the dub voice follows the unit
        same = nxt is not None and speaker_of(s[0]["s"], s[-1]["e"], segs) == speaker_of(nxt[0]["s"], nxt[-1]["e"], segs)
        if frag and nxt and same and dur + (nxt[-1]["e"] - nxt[0]["s"]) <= MAX_UNIT:
            continue
        units.append({"s": buf[0]["s"], "e": buf[-1]["e"], "en": fix_names(" ".join(x["w"] for x in buf)), "src_ids": ids})
        buf, ids = [], []
    return units, len(sents)


def copies_context(out, recent):
    """True when the reply mostly repeats an already-broadcast line (seen 09-30: an 11.6 s unit came back as the previous
    unit's translation once prior translations were in the prompt)."""
    a = set(out)
    return any(r and len(a & set(r)) / max(1, len(a | set(r))) > 0.8 for r in recent)


STOP = set("the a an and or but so that this these those there then than with from into onto over under about for "
           "of to in on at by as is are was were be been being have has had do does did not no yes it its it's they "
           "them their we our you your he his she her i me my just like well really very what which who whom whose "
           "when where why how will would could should can may might must shall also only even still yeah okay".split())


def content_words(t):
    return {w for w in re.findall(r"[a-z']+", t.lower()) if len(w) > 3 and w not in STOP}


def longest_common(a, b):
    best, prev = 0, [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else 0)
        best = max(best, max(cur))
        prev = cur
    return best


def echoed(out, en, units, zh, i, n=3, min_run=6, latin=True):
    """Indices j of recent units whose broadcast translation the reply repeats (a >= min_run-char run of zh[j]) although
    the English does not repeat it (< 3 shared content words): seen 09-30 on Sky, e.g. the previous line pasted in front
    of the new one. A speaker restarting a sentence repeats the English too, so that is not flagged.
    latin=False compares the Chinese characters only: product names kept in English ("Agents API", "OpenAI") recur
    from line to line and made 28 of 344 DevDay lines look copied (10-04, deepseek-flash replay)."""
    strip = (lambda t: t) if latin else (lambda t: re.sub(r"[A-Za-z0-9\s.\-]+", "", t))
    hits = []
    for j in range(max(0, i - n), i):
        if zh[j] and len(strip(zh[j])) >= min_run and longest_common(strip(out), strip(zh[j])) >= min_run \
                and len(content_words(en) & content_words(units[j]["en"])) < 3:
            hits.append(j)
    return hits


def prompt_index(units, i, n_prev):
    """Index-Translate: its own instruction format (the model card's "将以下文本翻译为…"), English-only context."""
    u = units[i]
    budget = max(MIN_BUDGET, int((u["e"] - u["s"]) * CPS))
    parts = [f"这是{DOMAIN}的语音识别结果，可能有识别错误，按上下文最可能的意思翻译。"]
    prev = " ".join(units[j]["en"] for j in range(max(0, i - n_prev), i))
    if prev:
        parts.append(f"上文（仅供理解，不要翻译）：{prev}")
    if i + 1 < len(units):
        parts.append("下文（仅供理解，不要翻译）：" + " ".join(x["en"] for x in units[i + 1:]))
    hits = [(k, v) for k, v in GLOSSARY.items() if re.search(rf"\b{re.escape(k)}\b", u["en"], re.I)][:10]
    if hits:
        parts.append("术语：" + "；".join(f"{k} 译为 {v}" for k, v in hits))
    parts.append(f"用简洁口语的中文直播字幕风格，不超过 {budget} 个字，句末不加句号，只输出译文。")
    return "\n".join(parts) + f"\n\n将以下文本翻译为简体中文：\n\n{u['en']}", budget


def shorten(en, zh, budget, port=None):
    """Index-Translate ignores length limits in the prompt fairly often (news replay: 126 of 563 over budget, vs ~50
    for Hy-MT2); asking it once more to compress the over-long ones brought that to 68. Keeps the original unless
    the new one is actually shorter and not empty."""
    if zh_len(zh) <= budget:
        return zh
    new = ask_llama(f"下面是一句英文直播的中文译文，用作同传配音，太长了念不完。在意思不变的前提下把它压缩到{budget}个字以内，"
                    f"可以删掉语气词、口头禅和重复，数字、年份、人名不能丢，只输出压缩后的译文。\n原文：{en}\n译文：{zh}", port)
    return new if new and zh_len(new) < zh_len(zh) else zh


def prompt(units, zh, i, with_prev_zh=True, drop=(), no_repeat=False, n_prev=None):
    u = units[i]
    n_prev = PREV_UNITS if n_prev is None else n_prev
    if STYLE == "index":
        return prompt_index(units, i, n_prev)
    budget = max(MIN_BUDGET, int((u["e"] - u["s"]) * CPS))
    tasks = [f"这是{DOMAIN}的语音识别结果，可能有识别错误，按上下文最可能的意思翻译。"]
    if with_prev_zh:
        prev = [f"{units[j]['en']} → {zh[j]}" for j in range(max(0, i - n_prev), i) if zh[j] and j not in drop]
        if prev:
            tasks.append("上文及其已播出的译文，仅供理解，不要翻译；称呼和术语要与之一致：" + " ｜ ".join(prev))
        if no_repeat:
            tasks.append("只翻译【待翻译文本】本身，不要重复上文已播出的译文。")
    elif i:
        tasks.append("上文，仅供理解，不要翻译：" + " ".join(units[j]["en"] for j in range(max(0, i - n_prev), i)))
    if i + 1 < len(units):  # every unit after i in `units` is look-ahead (live: the uncommitted tail, or the next units)
        tasks.append("下文，仅供理解，不要翻译：" + " ".join(x["en"] for x in units[i + 1:]))
    hits = [(k, v) for k, v in GLOSSARY.items() if re.search(rf"\b{re.escape(k)}\b", u["en"], re.I)][:10]
    if hits:
        tasks.append("参考下面的翻译：" + "；".join(f"{k} 翻译成 {v}" for k, v in hits))
    if TARGET_CPS:
        aim = max(4, int((u["e"] - u["s"]) * TARGET_CPS))
        tasks.append(f"译文用口语的中文直播字幕风格，约 {aim} 个字（这是配音稿，要和原话差不多长，不要过度精简），最多 {budget} 个字，句末不加句号。")
    else:
        tasks.append(f"译文用简洁口语的中文直播字幕风格，不超过 {budget} 个字，句末不加句号。")
    tasks.append("将【待翻译文本】翻译为简体中文。")
    body = "\n".join(f"{n}、{t}" for n, t in enumerate(tasks, 1))
    return f"【待翻译文本】\n{u['en']}\n\n【翻译任务】\n{body}", budget


def zh_len(t):
    return len(re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()…—-]", "", t))

def ask_llama(prompt_text, port=None, timeout=60, temperature=None):
    """One call to a local model through llama-server (Hy-MT2 or Index-Translate prompt style); replies that echo the
    task list come back empty."""
    port = port or settings.port("mt")
    if STYLE == "index":  # greedy (model card), no reasoning
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(
            {"messages": [{"role": "user", "content": prompt_text}], "temperature": 0, "max_tokens": 200,
             "chat_template_kwargs": {"enable_thinking": False}}).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.load(r)["choices"][0]["message"]["content"]
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().replace("\n", " ")
        return "" if "仅供理解" in out else out
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(
        {"messages": [{"role": "user", "content": prompt_text}],
         "temperature": TEMPERATURE if temperature is None else temperature, "top_p": 0.6, "top_k": 20,
         "repeat_penalty": 1.05, "max_tokens": 200}).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)["choices"][0]["message"]["content"].strip().replace("｜", "，")
    return "" if ("翻译任务" in out or "仅供理解" in out) else out