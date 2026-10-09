"""M0 TTS benchmark: IndexTTS-2.5 cross-lingual cloning (English reference -> Chinese) on DevDay segment B.

Runs in WSL with the index-tts repo venv. For each sentence (Chinese from the Hy-MT2 Q4 run, speaker from the
Nemotron low-latency run):
  1. natural pass: timbre = that speaker's 8 s reference, emotion = this sentence's own English audio
     (passed explicitly; left out, IndexTTS reuses the timbre clip as emotion reference);
  2. duration pass: duration_factor = target / natural length (only if within 0.7-1.4), target = sentence length.
Records load VRAM, per-sentence wall time / RTF and duration error, and writes a listening pack:
outputs/m0/tts/<tag>/<i>_orig.wav, <i>_nat.wav, <i>_dur.wav and index.md.

usage: python tts_indextts.py --tag bf16 [--fp32] [--n 20]
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import soundfile as sf
import torch

P = Path(__file__).resolve().parents[1]
SEG = Path.home() / "live-dub/seg"
MODEL = Path.home() / "models/IndexTTS-2.5"
REFS = {0: "Sam", 1: "Tejal", 2: "Romain", 3: "Codex voice"}


def gpu_used_mib():
    q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int(q.stdout.strip().splitlines()[0])


def speaker_of(s, e, segs):
    ov = {}
    for s2, e2, k in segs:
        o = min(e, e2) - max(s, s2)
        if o > 0:
            ov[k] = ov.get(k, 0) + o
    return max(ov, key=ov.get) if ov else None


def pick(sents, rows, segs, n):
    """n sentences spread over lengths, with a known speaker that has a reference clip."""
    cands = []
    for i, (s, r) in enumerate(zip(sents, rows)):
        k = speaker_of(s["s"], s["e"], segs)
        if k in REFS and r["zh"] and len(r["zh"]) < 60:
            cands.append((s["e"] - s["s"], i, k))
    cands.sort()
    step = max(1, len(cands) // n)
    return sorted(cands[::step][:n], key=lambda c: c[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True); ap.add_argument("--fp32", action="store_true")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--mt", default=str(P / "outputs/m0/mt_hymt2_B.json"))
    ap.add_argument("--all", action="store_true", help="every sentence with a known speaker, M1 duration policy")
    a = ap.parse_args()
    sys.path.insert(0, str(Path.home() / "live-dub/index-tts"))
    from indextts.infer_v2_5 import IndexTTS2

    mt = json.loads(Path(a.mt).read_text(encoding="utf-8"))
    sents, rows = mt["sentences"], mt["runs"]["q4"]["rows"]
    segs = json.loads((P / "outputs/m0/diar_nemotron_B.json").read_text(encoding="utf-8"))["runs"]["low_1.04s"]["segments"]
    audio, sr = sf.read(SEG / "B.48k.wav", dtype="float32")
    out = P / "outputs/m0/tts" / a.tag
    out.mkdir(parents=True, exist_ok=True)

    base = gpu_used_mib()
    t = time.time()
    tts = IndexTTS2(cfg_path=str(MODEL / "config.yaml"), model_dir=str(MODEL), use_bf16=not a.fp32,
                    use_cuda_kernel=False, use_deepspeed=False)
    load = {"load_s": round(time.time() - t, 1), "vram_after_load_mib": gpu_used_mib() - base}
    print("loaded", load, flush=True)
    torch.cuda.reset_peak_memory_stats()

    res = []
    if a.all:
        todo = [(s["e"] - s["s"], i, speaker_of(s["s"], s["e"], segs)) for i, s in enumerate(sents) if rows[i]["zh"]]
        todo = [t for t in todo if t[2] in REFS]
    else:
        todo = pick(sents, rows, segs, a.n)
    for dur, i, k in todo:
        s, zh = sents[i], re.sub(r"[。．.]$", "", rows[i]["zh"])
        lang = "zhen" if re.search(r"[A-Za-z]", zh) else "zh"
        orig = out / f"{i:02d}_orig.wav"
        sf.write(orig, audio[int((s["s"] - 0.05) * sr): int((s["e"] + 0.05) * sr)], sr)
        ref = str(SEG / "ref_B" / f"spk{k}.wav")
        row = {"i": i, "speaker": REFS[k], "en": s["en"], "zh": zh, "target_s": round(dur, 2), "lang": lang}
        try:
            f = out / f"{i:02d}_nat.wav"
            t = time.time()
            tts.infer(spk_audio_prompt=ref, emo_audio_prompt=str(orig), text=zh, output_path=str(f), lang=lang)
            wall = time.time() - t
            t0 = sf.info(f).duration
            row.update(nat_s=round(t0, 2), nat_wall=round(wall, 2), nat_rtf=round(wall / t0, 3))
            r = dur / t0
            if a.all:  # M1 policy (PLAN): 0.85-1.25 -> duration pass; >1.25 -> keep natural, end early; <0.85 -> squeeze to >=0.7, flag
                if r > 1.25:
                    row.update(factor=round(r, 3), use="nat", note="translation short: natural speed, ends early")
                    print(json.dumps(row, ensure_ascii=False), flush=True); res.append(row); continue
                if r < 0.85:
                    row["note"] = "translation long: retranslate shorter"
                    r = max(r, 0.7)
                row["use"] = "dur"
            if 0.7 <= r <= 1.4:
                f2 = out / f"{i:02d}_dur.wav"
                t = time.time()
                tts.infer(spk_audio_prompt=ref, emo_audio_prompt=str(orig), text=zh, output_path=str(f2), lang=lang,
                          duration_factor=r)
                wall = time.time() - t
                t1 = sf.info(f2).duration
                row.update(factor=round(r, 3), dur_s=round(t1, 2), dur_err=round((t1 - dur) / dur, 3),
                           dur_rtf=round(wall / t1, 3))
            else:
                row.update(factor=round(r, 3), skipped="ratio outside 0.7-1.4")
        except Exception as e:  # one bad sentence must not kill the run
            row["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(row, ensure_ascii=False), flush=True)
        res.append(row)

    ok = [r for r in res if "nat_rtf" in r]
    rtf = sorted(r["nat_rtf"] for r in ok)
    short = [r["nat_wall"] for r in ok if r["target_s"] < 2]
    errs = sorted(abs(r["dur_err"]) for r in res if "dur_err" in r)
    summ = dict(load, tag=a.tag, bf16=not a.fp32, n=len(res), failed=len(res) - len(ok),
                vram_peak_torch_mib=round(torch.cuda.max_memory_allocated() / 2**20),
                vram_after_run_mib=gpu_used_mib() - base,
                nat_rtf_p50=rtf[len(rtf) // 2] if rtf else None, nat_rtf_max=rtf[-1] if rtf else None,
                short_sentence_wall_s=short,
                dur_abs_err_p50=errs[len(errs) // 2] if errs else None, dur_abs_err_max=errs[-1] if errs else None)
    print("SUMMARY", json.dumps(summ, ensure_ascii=False), flush=True)
    (out / "result.json").write_text(json.dumps({"summary": summ, "rows": res}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    lines = [f"# IndexTTS-2.5 听审包（{a.tag}）", "",
             "每句三个文件：`_orig` 英文原声，`_nat` 自然语速的中文克隆，`_dur` 按原句时长重合成的版本。",
             "听三件事：像不像这个人；中文有没有外国口音；语气跟原声对不对得上。", "",
             "| # | 说话人 | 原句时长 | 自然 / 控时后 | 中文 | 英文 |", "| --- | --- | --- | --- | --- | --- |"]
    for r in res:
        lines.append(f"| {r['i']:02d} | {r['speaker']} | {r['target_s']} s | {r.get('nat_s', '-')} / {r.get('dur_s', '-')} s "
                     f"| {r['zh']} | {r['en']} |")
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
