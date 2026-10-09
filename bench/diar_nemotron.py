"""M0 diarization benchmark: Nemotron-3-Diarization on a DevDay segment, streaming (1.04 s) vs offline (30.4 s).

Runs in WSL (~/live-dub/asr/.venv). Reports wall time (RTF), VRAM, speakers found, speaker changes, and
compares the change count with the '>>' speaker-change marks of the live CC captions (rough reference only).
Configs come from the model card's latency table.

usage: python diar_nemotron.py SEG_WAV SEG_START_SEC VTT OUT_JSON
"""
import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import soundfile as sf
import torch

MODEL = Path.home() / "models/nvidia--Nemotron-3-Diarization/Nemotron-3-Diarization.nemo"
CONFIGS = {  # spkcache_len, fifo_len, chunk_len, right_context, update_period
    "low_1.04s": (264, 264, 9, 4, 222),
    "offline_30.4s": (264, 40, 340, 40, 300),
}


def gpu_used_mib() -> int:
    q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int(q.stdout.strip().splitlines()[0])


def cc_turns(vtt: Path, t0: float, t1: float) -> list[float]:
    """Times (relative to t0) of cues carrying the '>>' speaker-change mark."""
    turns, cur = [], None
    for line in vtt.read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\d\d):(\d\d):(\d\d\.\d+) -->", line)
        if m:
            cur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]); continue
        if cur is not None and t0 <= cur < t1 and "&gt;&gt;" in line:
            if not turns or cur - t0 - turns[-1] > 1.0:
                turns.append(round(cur - t0, 1))
    return turns


def parse(seg) -> tuple[float, float, int]:
    if isinstance(seg, str):
        s, e, spk = seg.replace(",", " ").split()[:3]
        return float(s), float(e), int(re.sub(r"\D", "", spk))
    s, e, spk = seg[:3]
    return float(s), float(e), int(re.sub(r"\D", "", str(spk)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav"); ap.add_argument("start", type=float); ap.add_argument("vtt"); ap.add_argument("out")
    ap.add_argument("--device", default="cuda"); ap.add_argument("--configs", default=",".join(CONFIGS))
    a = ap.parse_args()
    from nemo.collections.asr.models import SortformerEncLabelModel

    dur = sf.info(a.wav).duration
    base = gpu_used_mib()
    model = SortformerEncLabelModel.restore_from(restore_path=str(MODEL), map_location=a.device, strict=False).eval()
    model_mib = gpu_used_mib() - base
    result = {"device": a.device, "wav": a.wav, "audio_s": round(dur, 1), "vram_model_mib": model_mib,
              "cc_turns": cc_turns(Path(a.vtt), a.start, a.start + dur), "runs": {}}

    for name, (cache, fifo, chunk, rc, upd) in ((n, CONFIGS[n]) for n in a.configs.split(",")):
        sm = model.sortformer_modules
        sm.spkcache_len, sm.fifo_len, sm.chunk_len, sm.chunk_right_context, sm.spkcache_update_period = \
            cache, fifo, chunk, rc, upd
        model._check_streaming_parameters()
        torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
        t = time.time()
        segs = model.diarize(audio=[a.wav], batch_size=1)[0]
        torch.cuda.synchronize()
        wall = time.time() - t
        segs = sorted(parse(s) for s in segs)
        spk_time: dict[int, float] = {}
        for s, e, k in segs:
            spk_time[k] = spk_time.get(k, 0.0) + (e - s)
        changes = [round(segs[i][0], 1) for i in range(1, len(segs)) if segs[i][2] != segs[i - 1][2]]
        result["runs"][name] = {
            "wall_s": round(wall, 2), "rtf": round(wall / dur, 4),
            "vram_peak_torch_mib": round(torch.cuda.max_memory_allocated() / 2**20),
            "speakers": {f"spk{k}": round(v, 1) for k, v in sorted(spk_time.items())},
            "n_segments": len(segs), "n_changes": len(changes), "changes": changes,
            "segments": [[round(s, 2), round(e, 2), k] for s, e, k in segs],
        }
        print(name, json.dumps({k: v for k, v in result["runs"][name].items() if k not in ("segments", "changes")}))
    print("cc_turns", len(result["cc_turns"]), result["cc_turns"])
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
