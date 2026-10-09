"""Live processor: ingest chunks -> ASR windows -> committed sentences -> speaker IDs -> units -> translation -> events.
Translation is the config's mt_model (livedub.mt.models()): a local GGUF through llama-server (default index-9b),
started here if it is not running, or an online chat model through livedub/mt_chat.py (needs the provider's key).

Every STEP seconds the audio from the last commit point to the newest sample (at most WINDOW seconds) goes to the ASR
service. Sentences split at Parakeet's terminal punctuation are committed once they end at least TAIL_GUARD seconds
before the window end (the tail may still grow); an unpunctuated run is force-committed when the window is full.
Each committed sentence gets a TitaNet embedding and an online-clustered speaker ID (sentences under 1 s inherit the
previous speaker). Fragments are glued to the next sentence of the same speaker (livedub.mt rules), then translated
with the previous units (English -> broadcast Chinese) as context and the uncommitted tail as look-ahead.
Each unit's subtitle event is appended to the events JSONL at once; if the dubbing service (settings ports.dub, see
docs/接口.md) is up, the unit is also queued for TTS (earliest air time first) and, when the dub comes back in
time, the same event is appended again with "dub" and "gain" (readers replace an event with the same pt0). Air times
come from the run's anchor.json (written by ingest at the first chunk): PT p airs at T0 + D + (p - p0).
The voice reference per speaker is that speaker's first clip of 3 s or more, replaced by longer ones (up to 12 s)
until one of 6 s is held. Every unit's timing goes to a separate JSONL:
  lag = written - when the unit's end reached us, slack = playout deadline for the unit's start - written.

usage: python -m livedub.processor BUF EVENTS CONFIG [--anchor ANCHOR_JSON] [--log TIMING_JSONL] [--keep-dubs N]
"""
import argparse
import io
import json
import subprocess
import threading
import time
import urllib.request
import wave
from pathlib import Path

import av
import numpy as np

from . import mt, settings
from .playout import ChunkReader

SR, SR_HI = 16000, 24000
STEP, WINDOW, TAIL_GUARD, MIN_WINDOW = 2.0, 30.0, 1.0, 3.0
ASR_URL = settings.url("asr")
DUB_URL = settings.url("dub")
DUB_RMS = 0.08  # playout normalises dubs to this RMS; gain scales it to the original speech level
MIN_REF_S = 1.2  # shortest clip used as a TTS reference (the service rejects < 1 s)
lpath = settings.to_linux   # the speech services run on the Linux side (WSL on Windows) and read our files there


def post(url, obj, timeout=60):
    req = urllib.request.Request(url, json.dumps(obj).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class AudioRing:
    """Mono PCM of the program at 16 kHz (ASR) and 24 kHz (TTS references), indexed by PT; filled by a thread
    decoding the ingest chunks."""

    def __init__(self, buf):
        self.buf, self.base, self.lock, self.done = buf, None, threading.Lock(), False
        self.parts, self.n, self.hi = [], 0, []
        self.drop, self.drop_hi = 0, 0  # samples already discarded from the front (see trim)

    def run(self):
        src = av.open(io.BufferedReader(ChunkReader(self.buf), buffer_size=1 << 20), format="mpegts")
        rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        rs_hi = av.AudioResampler(format="flt", layout="mono", rate=SR_HI)
        for fr in src.decode(audio=0):
            if self.base is not None and fr.pts is not None:
                gap = float(fr.pts * fr.time_base) - (self.base + self.n / SR)
                if gap > 0.2:  # the source skipped (live restart): silence keeps sample index == PT
                    with self.lock:
                        self.parts.append(np.zeros(int(round(gap * SR)), np.float32)); self.n += int(round(gap * SR))
                        self.hi.append(np.zeros(int(round(gap * SR_HI)), np.float32))
                    print(f"audio gap of {gap:.2f}s at PT {self.base + self.n / SR - gap:.2f}", flush=True)
            hi = [f.to_ndarray().reshape(-1).astype(np.float32) for f in rs_hi.resample(fr)]
            for f in rs.resample(fr):
                x = f.to_ndarray().reshape(-1).astype(np.float32)
                with self.lock:
                    if self.base is None and f.pts is not None:
                        self.base = float(f.pts * f.time_base)
                    self.parts.append(x); self.n += len(x)
            with self.lock:
                self.hi += hi
        self.done = True

    @property
    def latest(self):
        with self.lock:
            return None if self.base is None else self.base + self.n / SR

    def get(self, t0, t1, hi=False):
        with self.lock:
            if hi:
                if len(self.hi) > 1:
                    self.hi = [np.concatenate(self.hi)]
                x, sr = (self.hi[0] if self.hi else np.zeros(0, np.float32)), SR_HI
            else:
                if len(self.parts) > 1:
                    self.parts = [np.concatenate(self.parts)]
                x, sr = (self.parts[0] if self.parts else np.zeros(0, np.float32)), SR
            d = self.drop_hi if hi else self.drop
            a, b = int(round((t0 - self.base) * sr)) - d, int(round((t1 - self.base) * sr)) - d
            return x[max(a, 0):max(b, 0)].copy()

    def trim(self, t):
        """Forget audio before PT t (a live run would otherwise keep ~575 MB per hour and re-join it every step)."""
        with self.lock:
            for attr, dattr, sr in (("parts", "drop", SR), ("hi", "drop_hi", SR_HI)):
                parts = getattr(self, attr)
                if not parts:
                    continue
                x = np.concatenate(parts)
                cut = int(round((t - self.base) * sr)) - getattr(self, dattr)
                if cut > 0:
                    x = x[cut:]; setattr(self, dattr, getattr(self, dattr) + cut)
                setattr(self, attr, [x])


def write_wav(path, x, sr):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


class VoiceRefs:
    """Per-speaker voice reference: first clip >= 3 s, replaced by longer ones (<= 12 s) until one >= 6 s is held.
    Each replacement is a new file, so a TTS request never sees a half-written reference."""

    def __init__(self, folder):
        self.folder, self.ref, self.last = folder, {}, None

    def offer(self, spk, x, sr):
        d = len(x) / sr
        cur = self.ref.get(spk)
        if d < 3.0 or d > 12.0 or (cur and (cur[1] >= 6.0 or d <= cur[1])):
            return
        path = self.folder / f"ref_spk{spk}_{len(x)}.wav"
        write_wav(path, x, sr)
        self.ref[spk] = (path, d)
        self.last = path

    def get(self, spk):
        return self.ref.get(spk, (None,))[0]


class Dubber:
    """One worker feeding the (serial) dubbing service, most urgent unit first; late dubs are dropped."""

    def __init__(self, write_event, log):
        self.write_event, self.log = write_event, log
        self.q, self.cv, self.ok = [], threading.Condition(), False
        try:
            self.ok = bool(json.load(urllib.request.urlopen(f"{DUB_URL}/health", timeout=2)).get("ok"))
        except Exception:
            pass
        if self.ok:
            threading.Thread(target=self.run, daemon=True).start()

    def submit(self, deadline, req, ev, out_win):
        with self.cv:
            self.q.append((deadline, req, ev, out_win)); self.q.sort(key=lambda r: r[0]); self.cv.notify()

    def run(self):
        while True:
            with self.cv:
                while not self.q:
                    self.cv.wait()
                deadline, req, ev, out_win = self.q.pop(0)
            rec = {"pt0": ev["pt0"], "dub": "skipped"}
            left = deadline - time.time()
            if left >= 2.0:
                t = time.time()
                try:
                    r = post(f"{DUB_URL}/synthesize", req, timeout=min(25.0, left - 0.5))
                    rec.update(dub="late" if time.time() > deadline else "ok", tts_s=round(time.time() - t, 3),
                               natural_s=r.get("natural_s"), factor=r.get("factor"), clamped=r.get("clamped"),
                               audio_s=r.get("audio_s"), target_s=req["target_s"])
                    if rec["dub"] == "ok":
                        self.write_event(dict(ev, dub=out_win))
                except Exception as ex:
                    rec.update(dub="failed", err=str(ex)[:200])
            rec["left_s"] = round(deadline - time.time(), 3)
            self.log(rec)


class Speakers:
    def __init__(self, thr):
        self.thr, self.cent, self.n, self.last = thr, [], [], None

    def assign(self, emb, dur):
        if dur < 1.0 and self.last is not None:
            return self.last
        emb = np.asarray(emb)
        if self.cent:
            sims = [float(emb @ c) for c in self.cent]
            k = int(np.argmax(sims))
            if sims[k] >= self.thr:
                c = self.cent[k] * self.n[k] + emb
                self.cent[k] = c / np.linalg.norm(c); self.n[k] += 1
                self.last = k
                return k
        self.cent.append(emb); self.n.append(1); self.last = len(self.cent) - 1
        return self.last


def split_sentences(words, max_len=12.0, back=4.0):
    """Cut at sentence-final punctuation; a run of max_len s without any is cut at the longest pause in its last `back`
    s (not right after the word that crossed the limit: that left 1-word orphans like "of sport." dubbed on their own)."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if w["w"].rstrip().endswith((".", "?", "!")):
            out.append(cur); cur = []
        elif cur[-1]["e"] - cur[0]["s"] >= max_len:
            ks = [k for k in range(1, len(cur)) if cur[k]["s"] >= cur[-1]["e"] - back]
            k = max(ks, key=lambda k: cur[k]["s"] - cur[k - 1]["e"]) if ks else len(cur)
            out.append(cur[:k]); cur = cur[k:]
    return out, cur


def is_fragment(ws):
    return ws[-1]["e"] - ws[0]["s"] < 1.5 or len(ws) < 4 or not ws[-1]["w"].rstrip().endswith((".", "?", "!"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("buf"); ap.add_argument("events"); ap.add_argument("config")
    ap.add_argument("--anchor", default=None); ap.add_argument("--log", default=None)
    ap.add_argument("--clips", default=".tmp/live/clips")
    ap.add_argument("--keep-dubs", type=int, default=0,
                    help="delete dub clips this many units back (0 = keep all; the app's player can rewind into them)")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    mt.TARGET_CPS = float(cfg.get("mt_target_cps", 5.0))
    mt.GLOSSARY = cfg.get("glossary", mt.GLOSSARY)
    mt.NAMES = cfg.get("names", mt.NAMES)
    mt.DOMAIN = cfg.get("domain", mt.DOMAIN)
    # 0.3, not the model card's 0.7: replayed on Sky (bench/mt_replay.py, docs/dev/实测-M2.md) it beat 0.7 on 10 of 58
    # judged lines and lost 2, and the odd one-off mistranslations at 0.7 did not recur
    mt.TEMPERATURE = float(cfg.get("mt_temperature", 0.3))
    # context = the previous 8 units' English only (replayed on Sky 09-30: beat 3 units with their broadcast
    # translations on 10 of 58 judged lines, lost 5; terms stayed as consistent; copying the previous line went away)
    mt.PREV_UNITS = int(cfg.get("mt_context_units", 8))
    key = cfg.get("mt_model") or settings.get()["mt"]["default"]
    model = mt.models()[key]
    mt.STYLE = model["style"]
    ctx_zh = bool(cfg.get("mt_context_zh", False))
    chat = None
    if model.get("api"):  # online model: the whole stream as one conversation (livedub/mt_chat.py), no llama-server
        from .mt_chat import ChatMT
        chat = ChatMT(model.get("model", ""), model["api"], domain=mt.DOMAIN, names=mt.NAMES, glossary=mt.GLOSSARY,
                      temperature=float(cfg.get("mt_api_temperature", 1.3)), ctx_window=model.get("ctx", 1_000_000),
                      compact_at=cfg.get("mt_compact_at"), log_path=Path(a.events).with_name("mt_api.jsonl"),
                      thinking=cfg.get("mt_api_thinking", "off"), prompt=cfg.get("mt_api_prompt", "v5"))
    delay = float(cfg.get("delay_s", 30))
    clips = Path(a.clips); clips.mkdir(parents=True, exist_ok=True)
    anchor_path = Path(a.anchor) if a.anchor else Path(a.events).with_name("anchor.json")
    log = open(a.log or Path(a.events).with_name("timing.jsonl"), "a", encoding="utf-8")

    mt_url = settings.url("mt")
    for _ in range(90):  # services must be up: ASR and llama-server (started here if missing)
        try:
            if json.load(urllib.request.urlopen(f"{ASR_URL}/health", timeout=2)).get("ok"):
                break
        except Exception:
            time.sleep(1)
    try:
        urllib.request.urlopen(f"{mt_url}/health", timeout=2)
        running = True
    except Exception:
        running = False
    if chat:
        print(f"translation: {model['model']} online, thinking {chat.thinking}, compaction at {chat.compact_at} prompt tokens",
              flush=True)
    elif running:  # a server left from another run may hold a different model: say so, don't silently use it
        try:
            path = json.load(urllib.request.urlopen(f"{mt_url}/props", timeout=2)).get("model_path", "")
            if Path(path).name != Path(mt.gguf_path(key)).name:
                print(f"WARNING: llama-server at {mt_url} runs {path}, config wants {mt.gguf_path(key)}; "
                      f"stop it to let the processor start the right one", flush=True)
        except Exception:
            pass
    else:
        subprocess.Popen(mt.llama_argv(key), stdout=subprocess.DEVNULL, stderr=open(clips / "llama.log", "w"))
        for _ in range(120):
            try:
                urllib.request.urlopen(f"{mt_url}/health", timeout=2); break
            except Exception:
                time.sleep(1)

    lock = threading.Lock()

    def log_rec(rec):
        with lock:
            log.write(json.dumps(rec, ensure_ascii=False) + "\n"); log.flush()

    def write_event(ev):
        with lock, open(a.events, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    refs = VoiceRefs(clips)
    dubber = Dubber(write_event, log_rec)
    if not cfg.get("dub", True):  # the session turned dubbing off: subtitles only
        dubber.ok = False
    print("dubbing service", "up" if dubber.ok else "not reachable: subtitles only", flush=True)
    ring = AudioRing(a.buf)
    threading.Thread(target=ring.run, daemon=True).start()
    spk = Speakers(float(cfg.get("speaker_threshold", 0.45)))
    units, zh = [], []           # emitted units and their translations (context for the next ones)
    pending, pending_since = [], None
    commit = None
    win_no = 0
    anchor = None

    def arrival(pt):
        return None if not anchor else anchor["T0"] + (pt - anchor["p0"])

    def emit(sent_group, tail_text):
        """Translate one unit and append its event."""
        words = [w for s in sent_group for w in s["words"]]
        u = {"s": words[0]["s"], "e": words[-1]["e"], "en": mt.fix_names(" ".join(w["w"] for w in words)),
             "spk": sent_group[0]["spk"]}
        seq = units + [u] + ([{"s": u["e"], "e": u["e"] + 3, "en": tail_text}] if tail_text else [])
        t = time.time()
        budget = max(mt.MIN_BUDGET, int((u["e"] - u["s"]) * mt.CPS))
        if chat:  # fix-ups are further turns of the same conversation, so its cached prefix never changes
            try:
                aim = max(4, int((u["e"] - u["s"]) * mt.TARGET_CPS)) if mt.TARGET_CPS else None
                out, _ = chat.translate(u["en"], tail_text, budget, aim, dur=u["e"] - u["s"])
                if mt.echoed(out, u["en"], seq, zh + [""], len(units), latin=False):
                    out, _ = chat.redo_no_repeat()
                if mt.zh_len(out) > budget + chat.slack:
                    new, _ = chat.shorten(budget, mt.zh_len(out))
                    out = new if new and mt.zh_len(new) < mt.zh_len(out) else out
            except Exception as ex:  # API down or too slow: this unit airs with the English line only
                print(f"MT API failed: {type(ex).__name__}: {ex}", flush=True)
                out = ""
        else:
            out = mt.ask_llama(mt.prompt(seq, zh + [""], len(units), with_prev_zh=ctx_zh)[0])
            hits = mt.echoed(out, u["en"], seq, zh + [""], len(units))
            if hits:  # repeats an already-broadcast line: again without that line in the context
                out = mt.ask_llama(mt.prompt(seq, zh + [""], len(units), with_prev_zh=ctx_zh, drop=set(hits),
                                              no_repeat=True)[0])
            if mt.STYLE == "index":
                out = mt.shorten(u["en"], out, budget)
        units.append(u); zh.append(out)
        ev = {"pt0": round(u["s"], 3), "pt1": round(u["e"], 3), "zh": out, "en": u["en"], "dub": None, "spk": u["spk"]}
        write_event(ev)
        now = time.time()
        if dubber.ok and anchor and out.strip() and u["e"] - u["s"] >= 0.8:
            n = len(units)
            (clips / f"unit_{n - 300:05d}.wav").unlink(missing_ok=True)  # long synthesized; voice references are kept
            if a.keep_dubs:
                (clips / f"dub_{n - a.keep_dubs:05d}.wav").unlink(missing_ok=True)
            x_hi = ring.get(u["s"] - 0.05, u["e"] + 0.05, hi=True)
            emo = clips / f"unit_{n:05d}.wav"
            write_wav(emo, x_hi, SR_HI)
            refs.offer(u["spk"], x_hi, SR_HI)
            spk_ref = refs.get(u["spk"]) or emo
            emo_ref = emo
            if len(x_hi) / SR_HI < MIN_REF_S:
                # the service rejects references under 1 s: a short unit borrows this speaker's
                # reference, or failing that the latest one of anyone (wrong voice beats no dub)
                emo_ref = spk_ref = refs.get(u["spk"]) or refs.last or emo
            x = ring.get(u["s"], u["e"])
            gain = float(np.clip(np.sqrt(np.mean(x ** 2)) / DUB_RMS, 0.3, 2.0)) if len(x) else 1.0
            out_wav = clips / f"dub_{n:05d}.wav"
            req = {"id": f"unit-{n:05d}", "text": out, "lang": "zhen" if any("a" <= c.lower() <= "z" for c in out) else "zh",
                   "spk_ref": lpath(spk_ref), "emo_ref": lpath(emo_ref), "target_s": round(u["e"] - u["s"], 3),
                   "out": lpath(out_wav)}
            deadline = anchor["T0"] + delay + (u["s"] - anchor["p0"]) - 1.0  # playout polls events every 0.5 s
            dubber.submit(deadline, req, dict(ev, gain=round(gain, 3)), str(out_wav.resolve()).replace("\\", "/"))
        rec = {"pt0": ev["pt0"], "pt1": ev["pt1"], "spk": u["spk"], "committed": round(sent_group[-1]["committed"], 3),
               "mt_s": round(now - t, 3), "written": round(now, 3)}
        if anchor:
            rec["lag_s"] = round(now - arrival(u["e"]), 3)                       # end of speech -> event written
            rec["slack_s"] = round(anchor["T0"] + delay + (u["s"] - anchor["p0"]) - now, 3)  # time left before air
        log_rec(rec)
        print(f"[{time.strftime('%H:%M:%S')}] spk{u['spk']} {u['s']:.1f}-{u['e']:.1f} lag {rec.get('lag_s')} "
              f"slack {rec.get('slack_s')} | {out}", flush=True)

    while True:
        time.sleep(STEP)
        if anchor is None and anchor_path.exists():
            anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        latest = ring.latest
        if latest is None:
            continue
        if commit is None:
            commit = ring.base
        if pending and time.time() - pending_since > 4.0:   # don't hold a fragment forever
            emit(pending, ""); pending = []
        if latest - commit < MIN_WINDOW:
            if ring.done:
                break
            continue
        t1 = min(latest, commit + WINDOW)
        x = ring.get(commit, t1)
        win_no += 1
        (clips / f"win_{win_no - 1:05d}.wav").unlink(missing_ok=True)  # ~1 MB a step: gigabytes over a live run
        wav = clips / f"win_{win_no:05d}.wav"
        write_wav(wav, x, SR)
        res = post(f"{ASR_URL}/asr", {"wav": settings.asr_path(wav)})
        words = [{"w": w["w"], "s": commit + w["s"], "e": commit + w["e"]} for w in res["words"]]
        sents, tail = split_sentences(words)
        full = t1 - commit >= WINDOW - 0.5
        if full and not words:  # a whole window of silence / music / gap padding: move past it, or we'd resend it forever
            commit = t1 - TAIL_GUARD
            ring.trim(commit - 90)
            continue
        done = [s for s in sents if s[-1]["e"] <= t1 - TAIL_GUARD]
        if full and not done and (sents or tail):  # no punctuation for a whole window: force a cut
            done = sents or [tail[: max(1, len(tail) * 2 // 3)]]
        rest = [w for s in sents[len(done):] for w in s] + ([] if (full and not sents) else tail)
        tail_text = " ".join(w["w"] for w in rest)
        if not done:
            if ring.done and latest - commit < WINDOW:
                done, tail_text = sents + ([tail] if tail else []), ""
                if not done:  # the source ended on silence: nothing left to commit (10-04: done[-1] crashed here)
                    break
            else:
                continue
        spans = [[s[0]["s"] - commit, s[-1]["e"] - commit] for s in done]
        embs = post(f"{ASR_URL}/embed", {"wav": settings.asr_path(wav), "spans": spans})["emb"]
        now = time.time()
        for s, e in zip(done, embs):
            sent = {"words": s, "spk": spk.assign(e, s[-1]["e"] - s[0]["s"]), "committed": now}
            if pending and pending[0]["spk"] == sent["spk"] and s[-1]["e"] - pending[0]["words"][0]["s"] <= 12.0:
                pending.append(sent)
                if not is_fragment(s):
                    emit(pending, tail_text); pending = []
                continue
            if pending:
                emit(pending, tail_text); pending = []
            if is_fragment(s):
                pending, pending_since = [sent], time.time()
            else:
                emit([sent], tail_text)
        commit = done[-1][-1]["e"]
        if win_no % 30 == 0:
            ring.trim(commit - 90)  # units reach back at most ~16 s before commit (held fragments); keep plenty
        if ring.done and latest - commit < 0.5:
            break
    if pending:
        emit(pending, "")
    while dubber.ok and dubber.q:
        time.sleep(1)
    time.sleep(5 if dubber.ok else 0)
    print("processor ended", flush=True)


if __name__ == "__main__":
    main()
