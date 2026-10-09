"""Dub speed-up bench (task: single-pass duration control + RTF), DevDay segment B, every sentence with a known speaker.

  --mode compare : per sentence, the M0 two-pass method (upstream IndexTTS2.infer natural, then infer with
                   duration_factor = clamp(target / natural)) vs dubsvc single pass (GPT once, factor from code length,
                   S2M + BigVGAN once). Same process, after warm-up, interleaved.
  --mode single  : single pass only (for the RTF knobs: --compile, --accel, --steps, --beams).

Translation from outputs/m0/mt_v2_B.json, speaker from Nemotron low-latency diarization, timbre ~/live-dub/seg/ref_B,
emotion = this sentence's own English audio. Writes outputs/m0/tts/<tag>/{<i>_orig,<i>_2p,<i>_1p}.wav + result.json.
Run from ~/live-dub/index-tts with its venv:  python tts_single_pass.py --tag sp_compare --mode compare
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P))
from dubsvc.engine import DubEngine, trim_silence, FACTOR_MIN, FACTOR_MAX  # noqa: E402

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


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))] if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--mode", choices=["compare", "single"], default="single")
    ap.add_argument("--compile", action="store_true")
    ap.add_argument("--accel", action="store_true")
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--beams", type=int, default=3)
    ap.add_argument("--n", type=int, default=0, help="limit sentences (0 = all)")
    ap.add_argument("--no-trim-aware", action="store_true")
    ap.add_argument("--edge-pad", type=float, default=0.0, help="seconds of edge silence assumed in natural length")
    ap.add_argument("--save", action="store_true", help="write wavs")
    ap.add_argument("--seed", type=int, default=None, help="torch.manual_seed(seed + i) before each single pass (A/B listening)")
    a = ap.parse_args()

    mt = json.loads((P / "outputs/m0/mt_v2_B.json").read_text(encoding="utf-8"))
    sents, rows = mt["sentences"], mt["runs"]["q4"]["rows"]
    segs = json.loads((P / "outputs/m0/diar_nemotron_B.json").read_text(encoding="utf-8"))["runs"]["low_1.04s"]["segments"]
    audio, sr = sf.read(SEG / "B.48k.wav", dtype="float32")
    out = P / "outputs/m0/tts" / a.tag
    orig_dir = P / "outputs/m0/tts/_orig_v2"
    out.mkdir(parents=True, exist_ok=True); orig_dir.mkdir(parents=True, exist_ok=True)

    todo = []
    for i, s in enumerate(sents):
        k = speaker_of(s["s"], s["e"], segs)
        if rows[i]["zh"] and k in REFS:
            todo.append((i, k))
    if a.n:
        todo = todo[: a.n]

    base = gpu_used_mib()
    t = time.time()
    eng = DubEngine(MODEL, diffusion_steps=a.steps, num_beams=a.beams, use_torch_compile=a.compile,
                    use_accel=a.accel, edge_pad_s=a.edge_pad)
    load = {"load_s": round(time.time() - t, 1), "vram_after_load_mib": gpu_used_mib() - base}
    print("loaded", load, flush=True)

    def orig_of(i):
        s = sents[i]
        f = orig_dir / f"{i:02d}.wav"
        if not f.exists():
            sf.write(f, audio[int((s["s"] - 0.05) * sr): int((s["e"] + 0.05) * sr)], sr)
        return str(f)

    # warm-up (also triggers compile / CUDA graph capture): two different lengths
    for i, k in todo[:2]:
        zh = re.sub(r"[。．.]$", "", rows[i]["zh"])
        eng.synth(zh, str(SEG / "ref_B" / f"spk{k}.wav"), orig_of(i), target_s=sents[i]["e"] - sents[i]["s"])
        if a.mode == "compare":
            eng.infer(spk_audio_prompt=str(SEG / "ref_B" / f"spk{k}.wav"), emo_audio_prompt=orig_of(i), text=zh,
                      output_path="/tmp/sp_warm.wav", lang="zh")
    torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
    print("warm", flush=True)

    res = []
    for i, k in todo:
        s = sents[i]
        zh = re.sub(r"[。．.]$", "", rows[i]["zh"])
        lang = "zhen" if re.search(r"[A-Za-z]", zh) else "zh"
        target = round(s["e"] - s["s"], 3)
        ref, emo = str(SEG / "ref_B" / f"spk{k}.wav"), orig_of(i)
        row = {"i": i, "speaker": REFS[k], "zh": zh, "target_s": target, "lang": lang}
        try:
            if a.mode == "compare":
                f = out / f"{i:02d}_2p_nat.wav"; f2 = out / f"{i:02d}_2p.wav"
                torch.cuda.synchronize(); t = time.perf_counter()
                eng.infer(spk_audio_prompt=ref, emo_audio_prompt=emo, text=zh, output_path=str(f), lang=lang)
                t0 = sf.info(f).duration
                r = min(max(target / t0, FACTOR_MIN), FACTOR_MAX)
                eng.infer(spk_audio_prompt=ref, emo_audio_prompt=emo, text=zh, output_path=str(f2), lang=lang,
                          duration_factor=r)
                torch.cuda.synchronize(); w2 = time.perf_counter() - t
                x2, _ = sf.read(f2, dtype="float32")
                tr2, _ = trim_silence(x2)
                row["two_pass"] = dict(wall_s=round(w2, 3), nat_s=round(t0, 3), factor=round(r, 3),
                                       clamped=r != target / t0, raw_s=round(len(x2) / 22050, 3),
                                       trim_s=round(len(tr2) / 22050, 3))
                if not a.save:
                    f.unlink(missing_ok=True); f2.unlink(missing_ok=True)
            if a.seed is not None:
                torch.manual_seed(a.seed + i)
            torch.cuda.synchronize(); t = time.perf_counter()
            o = eng.synth(zh, ref, emo, target_s=target, lang=lang, trim_aware=not a.no_trim_aware)
            w1 = time.perf_counter() - t
            wav = o.pop("wav")
            if a.save or a.mode == "compare":
                sf.write(out / f"{i:02d}_1p.wav", wav, o["sr"])
            o.pop("sr")
            o["edge_codes"] = list(o["edge_codes"])
            row["one_pass"] = dict(o, wall_s=round(w1, 3), rtf=round(w1 / o["raw_s"], 3),
                                   rtf_trim=round(w1 / max(o["audio_s"], 1e-3), 3))
        except Exception as e:  # one bad sentence must not kill the run
            import traceback; traceback.print_exc()
            row["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(row, ensure_ascii=False), flush=True)
        res.append(row)

    ok = [r for r in res if "one_pass" in r]
    one = [r["one_pass"] for r in ok]
    err = lambda got, r: (got - r["target_s"]) / r["target_s"]  # noqa: E731
    summ = dict(load, tag=a.tag, mode=a.mode, seed=a.seed, edge_pad=a.edge_pad, compile=a.compile, accel=a.accel, steps=a.steps, beams=a.beams,
                n=len(res), failed=len(res) - len(ok),
                vram_peak_torch_mib=round(torch.cuda.max_memory_allocated() / 2**20),
                vram_after_run_mib=gpu_used_mib() - base,
                rtf_p50=pct([o["rtf"] for o in one], .5), rtf_p90=pct([o["rtf"] for o in one], .9),
                short_wall_s=[o["wall_s"] for r, o in zip(ok, one) if r["target_s"] < 2],
                stage_sum_s={k: round(sum(o[k] for o in one), 2) for k in ("prep_s", "gpt_s", "s2m_s", "vocoder_s", "wall_s")},
                one_clamped=sum(o["clamped"] for o in one), no_stop=sum(not o["stop_hit"] for o in one))
    unc = [(r, r["one_pass"]) for r in ok if not r["one_pass"]["clamped"]]
    summ["one_err_raw_p50_unclamped"] = pct([abs(err(o["raw_s"], r)) for r, o in unc], .5)
    summ["one_err_trim_p50_unclamped"] = pct([abs(err(o["audio_s"], r)) for r, o in unc], .5)
    summ["one_err_trim_signed_p50_unclamped"] = pct([err(o["audio_s"], r) for r, o in unc], .5)
    summ["one_err_raw_p50_all"] = pct([abs(err(o["raw_s"], r)) for r, o in zip(ok, one)], .5)
    summ["one_err_trim_p50_all"] = pct([abs(err(o["audio_s"], r)) for r, o in zip(ok, one)], .5)
    if a.mode == "compare":
        two = [r["two_pass"] for r in ok]
        unc2 = [r for r in ok if not r["two_pass"]["clamped"]]
        summ.update(
            two_wall_total=round(sum(x["wall_s"] for x in two), 1),
            one_wall_total=round(sum(o["wall_s"] for o in one), 1),
            two_clamped=sum(x["clamped"] for x in two),
            two_err_raw_p50_unclamped=pct([abs(err(r["two_pass"]["raw_s"], r)) for r in unc2], .5),
            two_err_trim_p50_unclamped=pct([abs(err(r["two_pass"]["trim_s"], r)) for r in unc2], .5),
            two_err_raw_p50_all=pct([abs(err(r["two_pass"]["raw_s"], r)) for r in ok], .5),
            two_err_trim_p50_all=pct([abs(err(r["two_pass"]["trim_s"], r)) for r in ok], .5))
        summ["speedup_pct"] = round(100 * (1 - summ["one_wall_total"] / summ["two_wall_total"]), 1)
    print("SUMMARY", json.dumps(summ, ensure_ascii=False), flush=True)
    (out / "result.json").write_text(json.dumps({"summary": summ, "rows": res}, ensure_ascii=False, indent=1),
                                     encoding="utf-8")


if __name__ == "__main__":
    main()
