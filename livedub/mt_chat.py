"""Translation through an online chat API (any OpenAI-compatible /chat/completions) with the whole stream as context.
Tuned on DeepSeek (deepseek-flash, 10-04): thinking off by default, or a reasoning effort (preset mt_api_thinking).

One conversation per run, append only, so each request is the previous one plus its reply plus one new line, and a
provider with prefix caching covers almost all of it (DeepSeek bills cache hits at 2% of the input price):
  system     role, domain, names, glossary, rules (fixed for the run)
  user       【原文】<unit>  【下文，仅供理解】<uncommitted tail>  【字数】(v3: 这句配音约 D 秒，)约 A 字，最多 N 字
  assistant  <translation>
Fix-ups are further turns, never edits, so the prefix stays put: a reply that repeats an aired line is followed by
"only this line", a too-long one by "shorten to N". When a request's prompt passes compact_at tokens (default half
the model's context window), a background request summarizes everything but the last KEEP turns, and the history
becomes system + summary + those turns + whatever was added meanwhile (one cache miss, then caching resumes).
The wording lives in PROMPTS by version (preset mt_api_prompt), so a new one can be replayed against the old.

Providers (PROVIDERS, plus settings "online"): the key comes from .secret/<provider>.json {"api_key", optional
"base_url", "model"} or the provider's environment variable. Every call (tokens, cache hits, latency) goes to
log_path as JSONL.
"""
import json
import os
import re
import threading
import time
import urllib.request

from . import settings

PROVIDERS = {
    # deepseek_thinking: send DeepSeek's "thinking" switch (other APIs may reject unknown fields)
    "deepseek": {"base_url": "https://api.deepseek.com", "env": "DEEPSEEK_API_KEY", "deepseek_thinking": True,
                 "label": "DeepSeek", "key_url": "https://platform.deepseek.com/api_keys"},
    # anything that speaks the OpenAI chat API: base_url, model and key in .secret/custom.json
    "custom": {"base_url": "", "env": "LIVEDUB_API_KEY", "label": "自定义接口（OpenAI 兼容）"},
}


def provider(name: str) -> dict:
    """The provider's effective config: PROVIDERS < settings "online" < .secret/<name>.json; "api_key" if any."""
    p = {**PROVIDERS.get(name, {}), **settings.get()["online"].get(name, {})}
    f = settings.secret_file(name)
    if f.exists():
        try:
            sec = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            sec = {}
        p.update({k: v for k, v in sec.items() if v})
    if not p.get("api_key") and p.get("env") and os.environ.get(p["env"]):
        p["api_key"] = os.environ[p["env"]]
    return p


def problem(name: str, model: str = "") -> str | None:
    """Why this provider cannot be used yet, in words for the UI, or None."""
    p = provider(name)
    if not p.get("api_key"):
        return "未填写 API key（在「环境」页的线上翻译中填写，或写入 .secret/%s.json）" % name
    if not p.get("base_url"):
        return "未填写接口地址（base_url）"
    if not (model or p.get("model")):
        return "未填写模型名称"
    return None


PROMPTS = {
    # v2 (10-04 first live config): one system paragraph, target/limit per line, shorten on any overflow
    "v2": {
        "system": """你是{domain}的同传字幕译者。用户按时间顺序逐句发来语音识别出的英文，你把每次的【原文】译成简体中文，用作直播字幕和配音稿。
- 只输出这一句的译文：不解释，不加引号，句末不加句号；句中用中文逗号断句，不要用空格代替标点。
- 口语、自然、意思完整，像中文直播字幕；字数接近【字数】给的目标（这是配音稿，要和原话差不多长），不超过上限。
- 识别可能有错，按上下文最可能的意思翻；【下文】只帮助理解，不要翻进来。
- 不重复前面已经译过的内容，不补原文没有的信息；人名、术语前后一致。""",
        "limit": lambda dur, aim, budget: f"约 {min(aim, budget)} 字，最多 {budget} 字" if aim else f"不超过 {budget} 字",
        "echo": "这句译文重复了前面已经播出的内容。只翻译刚才那句【原文】本身，照原来的要求（中文逗号断句、字数），只输出译文。",
        "shorten": lambda n, budget: f"太长了，配音念不完。意思不变，压缩到 {budget} 字以内，数字、人名不能丢，用中文逗号断句，只输出压缩后的译文。",
        "slack": 0,
        "summary": "先不翻译。用不超过 800 字总结到目前为止的直播，供你之后继续翻译时参考：主题和要点、出场的人和称呼、已经用过的人名和术语译法。只输出摘要。",
        "summary_tokens": 1200,
    },
    # v3 (10-04): ranked priorities, ASR repair, half sentences stay half; the line says how long the dub is; shorten
    # says by how much and may only drop filler; 1 char over is let through; structured summary capped at 600 chars
    "v3": {
        "system": """你是{domain}的同传译员，给中文观众做转播。每句译文既是字幕，也由配音按原话的时长念出来。
用户按时间顺序一句句发来语音识别出的英文，你每次只翻译【原文】这一句。
按这个优先级：
1. 准确完整：数字、人名、否定、因果、转折不能丢，不加原文没有的信息。识别可能听错同音词、人名和专名，按上下文改正后再翻。
2. 念得完：字数接近【字数】的目标、不超过上限；要省字就删语气词、口头禅和重复，不删实际信息。
3. 像中文口语：顺口自然，不要翻译腔；称呼（你/你们）、人名、术语和前面的译文一致。
4. 原文是半截话就译成半截；【下文】只帮你理解，不要提前翻进来。
格式：只输出一行译文，不解释，不加引号；句中用中文逗号断句，句末不加句号；数字用阿拉伯数字。""",
        "limit": lambda dur, aim, budget: (f"这句配音约 {dur:.1f} 秒，" if dur else "")
                                          + (f"约 {min(aim, budget)} 字，最多 {budget} 字" if aim else f"最多 {budget} 字"),
        "echo": "这句和前面已经播出的译文重复了。只翻译刚才那句【原文】本身，其余要求不变，只输出译文。",
        "shorten": lambda n, budget: (f"现在 {n} 字，超过上限 {budget} 字了。只删语气词、重复和可省的修饰，数字、人名、否定、因果都保留，"
                                      f"压到 {budget} 字以内，用中文逗号断句，只输出译文。"),
        "slack": 1,
        "summary": ("先不翻译。把到目前为止的直播整理成参考摘要，供你之后继续翻译用，严格不超过 600 字，格式：\n"
                    "【场景】一句话\n【要点】最多 8 条，每条一句\n【人物】出场的人和称呼\n"
                    "【译法】人名、产品名、术语的原文 → 译法，每个一项\n只输出摘要。"),
        "summary_tokens": 900,
    },
}
# v4 (10-04): v3 plus what a blind v2/v3 judging turned up: repeated phrases and fillers translated literally,
# English ASR garbage left in the line, "you" to the audience as 你
PROMPTS["v4"] = dict(PROMPTS["v3"], system=PROMPTS["v3"]["system"].replace(
    "4. 原文是半截话就译成半截；【下文】只帮你理解，不要提前翻进来。",
    "4. 原文是半截话就译成半截；【下文】只帮你理解，不要提前翻进来。\n"
    "5. 口头禅、填充词和连说两遍的话（you know、I mean、like 之类）按中文习惯处理：重复的只译一次，不硬译成「就像」「你知道」。\n"
    "6. 识别出来不成词、和上下文对不上的英文，按上下文猜意思，猜不出就略过，别把英文乱码留在译文里。\n"
    "7. 台上对现场观众或屏幕前观众说的 you 译成「你们」，对某一个人说才用「你」。"))
# v5 (10-04, default): two blind judgings (100 + 120 pairs) found v3's and v4's reworded instructions no better than
# v2's (v2 won 40:31 both times, the rest ties), so v5 keeps v2's wording and takes only v3's mechanics: one char of
# slack before a shorten turn, a shorten turn that says by how much and may only drop filler, the structured summary
PROMPTS["v5"] = dict(PROMPTS["v2"], shorten=PROMPTS["v3"]["shorten"], slack=1, summary=PROMPTS["v3"]["summary"],
                     summary_tokens=PROMPTS["v3"]["summary_tokens"])
KEEP = 16  # turns kept verbatim through a compaction
HAN = "[" + chr(0x4E00) + "-" + chr(0x9FFF) + "]"


def clean(out):
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().replace("\n", " ")
    out = re.sub(r"^(译文|翻译)[:：]\s*", "", out).strip().strip("“”\"「」")
    out = re.sub(rf"(?<={HAN}),\s*|,\s*(?={HAN})", "，", out)  # an ASCII comma next to Chinese (seen 10-04)
    return re.sub(r"[。．]$", "", out)


class ChatMT:
    def __init__(self, model="deepseek-flash", provider_name="deepseek", domain="外语直播", names=(), glossary=None,
                 temperature=1.3, ctx_window=1_000_000, compact_at=None, log_path=None, timeout=12.0, thinking="off",
                 prompt="v5"):
        p = provider(provider_name)
        why = problem(provider_name, model)
        if why:
            raise RuntimeError(f"online translation ({provider_name}): {why}")
        self.url, self.key = p["base_url"].rstrip("/") + "/chat/completions", p["api_key"]
        self.model = p.get("model") or model   # a model name saved with the key wins over the table's
        self.deepseek_thinking = bool(p.get("deepseek_thinking"))
        self.temperature, self.timeout = temperature, timeout
        self.thinking = thinking or "off"   # off, or a reasoning_effort: low / high / max (temperature is then ignored)
        self.compact_at = int(compact_at or ctx_window // 2)
        self.p = PROMPTS[prompt]
        self.slack = self.p["slack"]   # chars a reply may run over before a shorten turn
        sys_text = self.p["system"].format(domain=domain)
        if names:
            sys_text += "\n人名和产品名照原文写：" + "、".join(names)
        if glossary:
            sys_text += "\n术语表：" + "；".join(f"{k} → {v}" for k, v in glossary.items())
        self.msgs = [{"role": "system", "content": sys_text}]
        self.lock = threading.Lock()
        self.compacting = False
        self.compactions, self.compact_retry = 0, 0.0
        self.gen = 0  # bumped by each compaction; a reply built on an older history must not trigger another
        self.log = open(log_path, "a", encoding="utf-8") if log_path else None

    def _log(self, rec):
        if self.log:
            with self.lock:
                self.log.write(json.dumps(rec, ensure_ascii=False) + "\n"); self.log.flush()

    def _call(self, msgs, kind, max_tokens=200):
        body = {"model": self.model, "messages": msgs, "temperature": self.temperature, "max_tokens": max_tokens}
        if self.thinking != "off":  # the reasoning counts against max_tokens; only the answer goes back into the history
            body.update(reasoning_effort=self.thinking, max_tokens=max_tokens + 4000)
        if self.deepseek_thinking:
            body["thinking"] = {"type": "disabled" if self.thinking == "off" else "enabled"}
        body = json.dumps(body).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json",
                                                                   "Authorization": "Bearer " + self.key})
        t = time.time()
        for attempt in (0, 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout if kind != "compact" else 120) as resp:
                    j = json.load(resp)
                break
            except Exception as e:
                if attempt:
                    self._log({"t": round(t, 3), "kind": kind, "error": f"{type(e).__name__}: {e}"[:300]})
                    raise
        u = j.get("usage") or {}
        hit = u.get("prompt_cache_hit_tokens")   # DeepSeek; OpenAI-style APIs report prompt_tokens_details.cached_tokens
        if hit is None:
            hit = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
        miss = u.get("prompt_cache_miss_tokens")
        if miss is None and hit is not None and u.get("prompt_tokens") is not None:
            miss = u["prompt_tokens"] - hit
        rec = {"t": round(t, 3), "kind": kind, "wall_s": round(time.time() - t, 3), "prompt": u.get("prompt_tokens"),
               "hit": hit, "miss": miss, "out": u.get("completion_tokens"), "msgs": len(msgs)}
        r = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        if r is not None:
            rec["reasoning"] = r
        self._log(rec)
        return j["choices"][0]["message"]["content"], rec

    def _turn(self, content, kind):
        with self.lock:
            msgs, gen = self.msgs + [{"role": "user", "content": content}], self.gen
        raw, rec = self._call(msgs, kind)
        out = clean(raw)
        with self.lock:
            self.msgs += [{"role": "user", "content": content}, {"role": "assistant", "content": out or raw}]
        if (rec["prompt"] or 0) >= self.compact_at and gen == self.gen and not self.compacting \
                and time.time() > self.compact_retry:
            self.compacting = True
            threading.Thread(target=self._compact, daemon=True).start()
        return out, rec

    def translate(self, en, tail, budget, aim=None, dur=None):
        content = f"【原文】{en}"
        if tail:
            content += f"\n【下文，仅供理解】{tail}"
        return self._turn(content + "\n【字数】" + self.p["limit"](dur, aim, budget), "mt")

    def redo_no_repeat(self):
        return self._turn(self.p["echo"], "echo")

    def shorten(self, budget, cur_len):
        return self._turn(self.p["shorten"](cur_len, budget), "shorten")

    def _compact(self):
        try:
            with self.lock:
                snap = list(self.msgs)
            cut = max(1, len(snap) - 2 * KEEP)
            while cut < len(snap) and snap[cut]["role"] != "user":
                cut += 1
            summary, _ = self._call(snap[:cut] + [{"role": "user", "content": self.p["summary"]}], "compact",
                                    self.p["summary_tokens"])
            with self.lock:
                tail = self.msgs[cut:]   # the kept turns plus everything added while summarizing
                self.msgs = [self.msgs[0],
                             {"role": "user", "content": "【此前内容摘要，仅供参考，不要翻译】\n" + summary.strip()},
                             {"role": "assistant", "content": "好的，我会按摘要里的人名和术语继续翻译。"}] + tail
                self.compactions += 1
                self.gen += 1
            self._log({"t": round(time.time(), 3), "kind": "compacted", "dropped_msgs": cut - 1,
                       "kept_msgs": len(tail), "summary_chars": len(summary), "summary": summary.strip()[:3000]})
        except Exception as e:
            self.compact_retry = time.time() + 60
            self._log({"t": round(time.time(), 3), "kind": "compact_failed", "error": str(e)[:300]})
        finally:
            self.compacting = False
