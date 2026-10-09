"""Where does IndexTTS-2.5's ~1 s per-call overhead go? Same sentence, three emotion-reference setups, 3 calls each:
  a) a different emotion clip every call (what the pipeline does now), b) the same clip every call, c) no emotion clip.
Runs in WSL with the index-tts venv, after tts_indextts.py has written outputs/m0/tts/bf16/*_orig.wav.
"""
import json
import sys
import time
from pathlib import Path

import soundfile as sf

P = Path(__file__).resolve().parents[1]
MODEL = Path.home() / "models/IndexTTS-2.5"
sys.path.insert(0, str(Path.home() / "live-dub/index-tts"))
from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

tts = IndexTTS2(cfg_path=str(MODEL / "config.yaml"), model_dir=str(MODEL), use_bf16=True, use_cuda_kernel=False)
ref = str(Path.home() / "live-dub/seg/ref_B/spk1.wav")
text = "横轴是延迟，要让它降下来，纵轴是性能"
origs = [str(P / f"outputs/m0/tts/bf16/{i:02d}_orig.wav") for i in (13, 19, 20)]
tmp = "/tmp/tts_overhead.wav"
tts.infer(spk_audio_prompt=ref, emo_audio_prompt=origs[0], text=text, output_path=tmp, lang="zh")  # warm-up

res = {}
for name, emos in (("a_new_emo_each_call", origs), ("b_same_emo", [origs[0]] * 3), ("c_no_emo", [None] * 3)):
    walls = []
    for e in emos:
        t = time.time()
        tts.infer(spk_audio_prompt=ref, emo_audio_prompt=e, text=text, output_path=tmp, lang="zh")
        walls.append(round(time.time() - t, 2))
    res[name] = {"wall_s": walls, "audio_s": round(sf.info(tmp).duration, 2)}
    print(name, res[name], flush=True)
(P / "outputs/m0/tts_overhead.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
