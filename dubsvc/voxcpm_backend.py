"""VoxCPM2 backend for dubsvc (10-04): openbmb/VoxCPM2 (2B, 48 kHz out) in this process (its own venv,
scripts/linux/setup_voxcpm.sh).

Cloning is VoxCPM2's "controllable" mode: reference_wav_path = the speaker's reference clip, no transcript needed, so an
English reference voices Chinese text directly. VoxCPM2 has no duration input, so the duration control is done after
the fact: synthesize at natural pace, trim, factor = target_s / natural length (clamped 0.85-1.25 like the IndexTTS
path), then WSOLA time-stretch (audiotsm). emo_ref is not used: the model takes one reference.
"""
import time
from pathlib import Path

import numpy as np

from dubsvc.audio import trim_silence

FMIN, FMAX = 0.85, 1.25


def stretch(x, factor):
    """Make x factor times as long (WSOLA; fine for speech within ~0.8-1.25)."""
    from audiotsm import wsola
    from audiotsm.io.array import ArrayReader, ArrayWriter
    w = ArrayWriter(channels=1)
    wsola(channels=1, speed=1.0 / factor).run(ArrayReader(x[np.newaxis, :].astype(np.float32)), w)
    return np.ascontiguousarray(w.data[0], dtype=np.float32)


class VoxCPMBackend:
    def __init__(self, model_dir, steps=10, cfg=2.0, normalize=True, optimize=True):
        from voxcpm import VoxCPM
        self.m = VoxCPM(voxcpm_model_path=str(Path(model_dir).expanduser()), enable_denoiser=False, optimize=optimize)
        self.sr = int(getattr(self.m.tts_model, "sample_rate", 48000))
        self.steps, self.cfg, self.normalize = steps, cfg, normalize

    def synth(self, text, spk_ref, emo_ref=None, target_s=None, lang="zh", keep_ms=30):
        t0 = time.time()
        x = self.m.generate(text=text, reference_wav_path=str(spk_ref), cfg_value=self.cfg,
                            inference_timesteps=self.steps, normalize=self.normalize, denoise=False,
                            retry_badcase=True, retry_badcase_max_times=2)
        gen_s = time.time() - t0
        x = np.asarray(x, dtype=np.float32).reshape(-1)
        raw_s = len(x) / self.sr
        x, lead = trim_silence(x, self.sr, keep_ms=keep_ms)
        natural = len(x) / self.sr
        factor, clamped = 1.0, False
        if target_s and natural > 0:
            f = float(target_s) / natural
            factor = min(max(f, FMIN), FMAX)
            clamped = factor != f
            if abs(factor - 1.0) > 0.01:
                x = stretch(x, factor)
        return dict(wav=x, sr=self.sr, natural_s=round(natural, 3), factor=round(factor, 3), clamped=clamped,
                    raw_s=round(raw_s, 3), audio_s=round(len(x) / self.sr, 3), lead_cut_s=round(lead, 3),
                    gen_s=round(gen_s, 3))
