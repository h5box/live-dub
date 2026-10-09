"""M0 ASR benchmark: Parakeet-TDT-0.6B-v2 on a 10-minute DevDay segment.

Runs in WSL (~/live-dub/asr/.venv). Chunks the 16 kHz mono wav into fixed windows, transcribes them in
batches with word timestamps, and reports wall time (RTF), peak VRAM, and a rough WER against the live
CC captions of the same time range (those are real-time stenography, so they carry their own errors).

usage: python asr_parakeet.py SEG_WAV SEG_START_SEC VTT OUT_JSON [--chunk 20] [--batch 8]
"""
import argparse
import html
import json
import re
import subprocess
import time
from pathlib import Path

import jiwer
import soundfile as sf
import torch

MODEL = Path.home() / "models/nvidia--parakeet-tdt-0.6b-v2/parakeet-tdt-0.6b-v2.nemo"


def vtt_text(vtt: Path, t0: float, t1: float) -> str:
    """Caption text of the cues that start inside [t0, t1), '>>' speaker marks removed."""
    out, keep = [], False
    for line in vtt.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\d\d):(\d\d):(\d\d\.\d+) -->", line)
        if m:
            s = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
            keep = t0 <= s < t1
            continue
        if keep and line.strip() and not line.startswith(("WEBVTT", "Kind:", "Language:")):
            out.append(html.unescape(line).replace(">>", " "))
    # live 608 captions repeat roll-up lines; drop exact consecutive duplicates
    dedup = [l for i, l in enumerate(out) if i == 0 or l != out[i - 1]]
    return " ".join(dedup)


NORM = jiwer.Compose([
    jiwer.ToLowerCase(), jiwer.RemovePunctuation(), jiwer.RemoveMultipleSpaces(), jiwer.Strip(),
    jiwer.ReduceToListOfListOfWords(),
])


def gpu_used_mib() -> int:
    q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int(q.stdout.strip().splitlines()[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav"); ap.add_argument("start", type=float); ap.add_argument("vtt"); ap.add_argument("out")
    ap.add_argument("--chunk", type=float, default=20.0); ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--boost", default="", help="comma-separated key phrases (speaker names, products) for GPU-PB context biasing")
    ap.add_argument("--boost-alpha", type=float, default=1.0)
    a = ap.parse_args()

    import nemo.collections.asr as nemo_asr

    base_mib = gpu_used_mib()
    t = time.time()
    model = nemo_asr.models.ASRModel.restore_from(str(MODEL), map_location=a.device).eval()
    load_s = time.time() - t
    model_mib = gpu_used_mib() - base_mib
    if a.boost:  # NeMo 3.0 TDT greedy_batch supports a boosting tree (key phrases)
        from omegaconf import open_dict
        dec = model.cfg.decoding
        with open_dict(dec):
            dec.strategy = "greedy_batch"
            dec.greedy.boosting_tree = {"key_phrases_list": [p.strip() for p in a.boost.split(",") if p.strip()],
                                        "context_score": 1.0, "depth_scaling": 2.0}
            dec.greedy.boosting_tree_alpha = a.boost_alpha
        model.change_decoding_strategy(dec)

    audio, sr = sf.read(a.wav, dtype="float32")
    assert sr == 16000 and audio.ndim == 1
    dur = len(audio) / sr
    n = int(a.chunk * sr)
    chunks = [audio[i:i + n] for i in range(0, len(audio), n)]

    # warm-up on one chunk so CUDA init / kernel selection is not counted
    model.transcribe([chunks[0]], batch_size=1, timestamps=True, verbose=False)
    torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()

    t = time.time()
    hyps = model.transcribe(chunks, batch_size=a.batch, timestamps=True, verbose=False)
    torch.cuda.synchronize()
    wall = time.time() - t
    peak_torch = torch.cuda.max_memory_allocated() / 2**20
    peak_smi = gpu_used_mib() - base_mib

    words, text = [], []
    for ci, h in enumerate(hyps):
        off = ci * a.chunk
        text.append(h.text)
        for w in (h.timestamp or {}).get("word", []):
            words.append({"w": w["word"], "s": round(off + w["start"], 3), "e": round(off + w["end"], 3)})
    hyp = " ".join(text)
    ref = vtt_text(Path(a.vtt), a.start, a.start + dur)
    m = jiwer.process_words(ref, hyp, reference_transform=NORM, hypothesis_transform=NORM)

    summary = {
        "model": "parakeet-tdt-0.6b-v2", "wav": a.wav, "audio_s": round(dur, 1), "chunk_s": a.chunk,
        "batch": a.batch, "load_s": round(load_s, 1), "wall_s": round(wall, 2), "rtf": round(wall / dur, 4),
        "vram_model_mib": model_mib, "vram_peak_torch_mib": round(peak_torch), "vram_peak_smi_delta_mib": peak_smi,
        "wer_vs_live_cc": round(m.wer, 4), "ref_words": len(NORM(ref)[0]) if ref else 0, "hyp_words": len(words),
        "torch": torch.__version__, "device": a.device, "boost": a.boost, "boost_alpha": a.boost_alpha, "threads": torch.get_num_threads(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": summary, "text": hyp, "words": words}, ensure_ascii=False),
                           encoding="utf-8")


if __name__ == "__main__":
    main()
