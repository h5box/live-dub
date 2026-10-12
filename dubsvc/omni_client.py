"""Client for the patched vLLM-Omni IndexTTS-2.5 server (started by the backend, 127.0.0.1:<ports.omni>).

The patch (dubsvc/omni/vllm-omni-v0.30.0-target_s.patch) adds extra_params.target_s: stage 1 computes the factor
from the GPT code count (clamped 0.85-1.10) and runs S2M + BigVGAN once, and appends one JSON line per request to
$LIVEDUB_OMNI_FACTOR_LOG. Reading the last line back is only valid because dubsvc sends one request at a time.
Pure stdlib + numpy + soundfile, so it runs in any venv that has those (the vLLM-Omni one does).
"""
import base64
import io
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

from dubsvc.audio import trim_silence

FACTOR_LOG = Path("/tmp/livedub_omni_factor.jsonl")


class OmniBackend:
    def __init__(self, url="http://127.0.0.1:8093", model="", timeout=60):
        # model: the path vLLM-Omni serves the weights from, which is also the model name it answers to
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self._b64 = {}

    def healthy(self):
        try:
            with urllib.request.urlopen(self.url + "/health", timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    def _data_url(self, path):
        st = Path(path).stat()
        key = (str(path), st.st_mtime, st.st_size)
        v = self._b64.get(key)
        if v is None:
            v = "data:audio/wav;base64," + base64.b64encode(Path(path).read_bytes()).decode()
            if len(self._b64) > 32:
                self._b64.clear()
            self._b64[key] = v
        return v

    def _last_factor(self, since):
        try:
            with open(FACTOR_LOG, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 4096))
                line = f.read().decode(errors="ignore").strip().splitlines()[-1]
            rec = json.loads(line)
            return rec if rec.get("t", 0) >= since else None
        except Exception:
            return None

    def synth(self, text, spk_ref, emo_ref=None, target_s=None, lang="zh", keep_ms=30, trim=True):
        extra = {"lang": lang}
        if emo_ref:
            extra["emo_audio"] = self._data_url(emo_ref)
        if target_s:
            extra["target_s"] = float(target_s)
        body = {"model": self.model, "input": text, "response_format": "wav", "ref_audio": self._data_url(spk_ref),
                "speed": 1.0, "extra_params": extra}
        req = urllib.request.Request(self.url + "/v1/audio/speech", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = r.read()
        wall = time.time() - t0
        x, sr = sf.read(io.BytesIO(data), dtype="float32")
        if x.ndim > 1:
            x = x.mean(axis=1)
        raw_s = len(x) / sr
        rec = self._last_factor(t0 - 1) or {}
        lead = 0.0
        if trim:
            x, lead = trim_silence(np.ascontiguousarray(x), sr, keep_ms=keep_ms)
        return dict(wav=x, sr=sr, natural_s=rec.get("natural_s"), factor=rec.get("factor"),
                    clamped=bool(rec.get("clamped", False)), raw_s=round(raw_s, 3), audio_s=round(len(x) / sr, 3),
                    lead_cut_s=round(lead, 3), n_codes=rec.get("codes"), omni_s=round(wall, 3))
