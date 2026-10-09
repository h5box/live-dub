"""ASR + speaker-embedding service for the live processor (Linux: WSL 2 on Windows; CPU by default).

Parakeet-TDT-0.6B-v2 with GPU-PB key-phrase boosting (names/products from the preset's asr_boost, alpha 2.0 — on CPU
the boosting tree runs without Triton) and TitaNet-L speaker embeddings, both loaded once. Stdlib HTTP, one request at
a time, 127.0.0.1 only. The backend starts it with the paths from settings (asr.*); scripts/linux/setup_asr.sh makes
the venv and downloads the models.

  GET  /health                                  -> {"ok": true, "boost": [...]}
  POST /asr   {"wav": "/path/as/seen/here.wav"}  -> {"words": [{"w","s","e"}], "wall_s"}   (times relative to the wav)
  POST /embed {"wav": "...", "spans": [[s, e]]}  -> {"emb": [[...192 floats]], "wall_s"}

usage: python asr_service.py --config PRESET.json --model parakeet-tdt-0.6b-v2.nemo --speaker-model titanet-large.nemo
                             [--port 8091] [--device cpu] [--threads 8]
"""
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf
import torch

LOCK = threading.Lock()
STATE = {}


def load(cfg, device, asr_path, spk_path):
    import nemo.collections.asr as nemo_asr
    from nemo.collections.asr.models import EncDecSpeakerLabelModel
    from omegaconf import open_dict
    asr = nemo_asr.models.ASRModel.restore_from(str(asr_path), map_location=device).eval()
    boost = cfg.get("asr_boost", [])
    if boost:
        dec = asr.cfg.decoding
        with open_dict(dec):
            dec.strategy = "greedy_batch"
            dec.greedy.boosting_tree = {"key_phrases_list": boost, "context_score": 1.0, "depth_scaling": 2.0,
                                        "use_triton": device != "cpu"}
            dec.greedy.boosting_tree_alpha = float(cfg.get("asr_boost_alpha", 2.0))
        asr.change_decoding_strategy(dec)
    spk = EncDecSpeakerLabelModel.restore_from(str(spk_path), map_location=device).eval()
    STATE.update(asr=asr, spk=spk, boost=boost)


def do_asr(req):
    audio, sr = sf.read(req["wav"], dtype="float32")
    assert sr == 16000
    with torch.no_grad():
        h = STATE["asr"].transcribe([audio], batch_size=1, timestamps=True, verbose=False)[0]
    return {"words": [{"w": w["word"], "s": round(w["start"], 3), "e": round(w["end"], 3)}
                      for w in (h.timestamp or {}).get("word", [])]}


def do_embed(req):
    audio, sr = sf.read(req["wav"], dtype="float32")
    out = []
    for s, e in req["spans"]:
        x = audio[int(s * sr): int(e * sr)]
        with torch.no_grad():
            emb, _ = STATE["spk"].infer_segment(x)
        v = emb.squeeze().cpu().numpy()
        out.append([round(float(f), 5) for f in v / np.linalg.norm(v)])
    return {"emb": out}


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200 if STATE else 503, {"ok": bool(STATE), "boost": STATE.get("boost", [])})
        else:
            self._send(404, {})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        fn = {"/asr": do_asr, "/embed": do_embed}.get(self.path)
        if not fn or not STATE:
            return self._send(404 if not fn else 503, {})
        t = time.time()
        try:
            with LOCK:
                res = fn(req)
            res["wall_s"] = round(time.time() - t, 3)
            self._send(200, res)
        except Exception as ex:
            self._send(500, {"error": f"{type(ex).__name__}: {ex}"})

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True); ap.add_argument("--port", type=int, default=8091)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--threads", type=int, default=8, help="CPU threads for torch (capped at the cores there are)")
    ap.add_argument("--model", default="~/live-dub/models/parakeet-tdt-0.6b-v2.nemo")
    ap.add_argument("--speaker-model", default="~/live-dub/models/titanet-large.nemo")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    torch.set_num_threads(max(1, min(a.threads, os.cpu_count() or a.threads)))
    srv = ThreadingHTTPServer((a.host, a.port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"listening on {a.host}:{a.port}, loading models ...", flush=True)
    load(cfg, a.device, Path(a.model).expanduser(), Path(a.speaker_model).expanduser())
    print(f"ready (boost: {len(STATE['boost'])} phrases)", flush=True)
    from nemo.utils import logging as nemo_logging
    nemo_logging.setLevel(nemo_logging.WARNING)   # NeMo logs a line per transcribe call: keep the log readable
    threading.Event().wait()


if __name__ == "__main__":
    main()
