"""live-dub dubbing service (docs/接口.md): IndexTTS-2.5 kept resident, one request at a time.

  GET  /health      503 while loading, then {"ok": true, "model": "IndexTTS-2.5", "bf16": true, "vram_mib": ..., "impl": ...}
  POST /synthesize  {"id", "text", "lang", "spk_ref", "emo_ref"|null, "target_s"|null, "out"}
                    -> {"id", "out", "sr", "natural_s", "factor", "clamped", "audio_s", "wall_s", "queue_s"}

Single pass: GPT once, factor = target_s / natural length (clamped 0.85-1.25), S2M + BigVGAN once; output trimmed
(30 ms kept before the onset). Three backends:
  --backend omni  (default, fastest): proxy to the patched vLLM-Omni server (--omni-url; the backend starts both,
                    see livedub/server/services.py; scripts/linux/setup_dub.sh installs them)
  --backend local : IndexTTS2 in this process (dubsvc/engine.py, the index-tts checkout --indextts-repo),
                    optionally --accel (flash-attn) / --compile
  --backend voxcpm: VoxCPM2 in this process (dubsvc/voxcpm_backend.py); duration by time-stretch after synthesis,
                    emo_ref unused
Requests are served strictly in arrival order by one worker thread; a request not done
within --timeout seconds gets 504 (the worker finishes it anyway and drops the result; a request still waiting in the
queue when its caller gave up is skipped).

Paths in requests are as this process sees them (Linux paths; under WSL the processor sends /mnt/<drive>/...).
  python dubsvc/server.py [--backend omni|local|voxcpm] [--port 8092] [--model ~/live-dub/models/IndexTTS-2.5] ...
"""
import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STATE = {"engine": None, "error": None, "info": {}}
JOBS = queue.Queue()


def gpu_used_mib():
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
        return int(q.stdout.strip().splitlines()[0])
    except Exception:
        return None


WARM = (("大家好，欢迎收看今天的发布会", 3.0), ("好的", 0.8), ("接下来我们看一下这个模型在真实任务上的表现", 5.0))


def synthetic_ref(path="/tmp/livedub_warmup_ref.wav", sr=24000, secs=4.0):
    """A speech-like noise clip (syllable-rate bursts) to warm the model up with when no real clip is given: the first
    requests compile and capture graphs, which depends on shapes, not on whose voice it is. The output is discarded."""
    import numpy as np
    import soundfile as sf
    rng = np.random.default_rng(0)
    t = np.arange(int(sr * secs)) / sr
    x = np.convolve(rng.standard_normal(len(t)), np.ones(12) / 12, mode="same")      # dull the hiss a little
    env = np.clip(np.sin(2 * np.pi * 4.0 * t), 0, None) ** 2 * (0.6 + 0.4 * np.sin(2 * np.pi * 0.5 * t))
    sf.write(path, (0.3 * x * env / (np.abs(x).max() + 1e-9)).astype(np.float32), sr)
    return path


def load(a):
    try:
        ref = str(Path(a.warmup_ref).expanduser()) if a.warmup_ref else None
        if ref and not Path(ref).exists():
            ref = None
        if ref is None:
            try:
                ref = synthetic_ref()
            except Exception as e:
                print("no warm-up clip:", e, flush=True)
        t = time.time()
        model = "IndexTTS-2.5"
        if a.backend == "omni":
            from dubsvc.omni_client import OmniBackend
            be = OmniBackend(a.omni_url, model=str(Path(a.model).expanduser()))
            while not be.healthy():  # omni takes ~2-3 min to start (compile, CUDA graphs)
                if time.time() - t > a.omni_wait:
                    raise RuntimeError(f"vLLM-Omni at {a.omni_url} not healthy after {a.omni_wait} s")
                time.sleep(3)
            if ref:
                for txt, tg in WARM:  # first requests trigger torch.compile / graph capture in omni
                    be.synth(txt, ref, None, target_s=tg)
            try:
                base = int(Path("/tmp/livedub_omni_base_mib").read_text().strip())
                used = gpu_used_mib()
                vram = used - base if used is not None else None
            except Exception:
                vram = None
            impl = "vllm-omni 0.30.0 (patched target_s), steps=25, stage0 no beams"
        elif a.backend == "voxcpm":
            from importlib.metadata import version
            from dubsvc.voxcpm_backend import VoxCPMBackend
            base = gpu_used_mib()
            be = VoxCPMBackend(a.voxcpm_model, steps=a.vox_steps, cfg=a.vox_cfg, optimize=a.vox_compile)
            if ref:
                for txt, tg in WARM:
                    be.synth(txt, ref, None, target_s=tg)
            used = gpu_used_mib()
            vram = (used - base) if (used is not None and base is not None) else None
            impl = (f"voxcpm {version('voxcpm')} steps={a.vox_steps} cfg={a.vox_cfg}"
                    f"{' compiled' if a.vox_compile else ''}, wsola stretch")
            model = "VoxCPM2"
        else:
            import torch
            from dubsvc.engine import DubEngine
            base = gpu_used_mib()
            be = DubEngine(Path(a.model).expanduser(), diffusion_steps=a.steps, num_beams=a.beams,
                           use_accel=a.accel, use_torch_compile=a.compile, max_mel_tokens=a.max_mel_tokens)
            if ref:
                for txt, tg in WARM:
                    be.synth(txt, ref, None, target_s=tg)
                be._spk_cache.clear(); be._emo_cache.clear()
            used = gpu_used_mib()
            vram = (used - base) if (used is not None and base is not None) else round(torch.cuda.memory_reserved() / 2**20)
            impl = "local " + "+".join([x for x, on in (("accel", a.accel), ("compile", a.compile)) if on] or ["eager"])
            impl += f" steps={a.steps} beams={1 if a.accel else a.beams}"
        STATE["info"] = {"model": model, "bf16": True, "impl": impl, "vram_mib": vram,
                         "load_s": round(time.time() - t, 1)}
        STATE["engine"] = be
        print("ready", json.dumps(STATE["info"], ensure_ascii=False), flush=True)
    except Exception:
        STATE["error"] = traceback.format_exc()
        print(STATE["error"], flush=True)


def worker():
    import soundfile as sf
    while True:
        job = JOBS.get()
        if job["abandoned"]:
            job["done"].set(); continue
        r = job["req"]
        t0 = time.time()
        try:
            eng = STATE["engine"]
            text = r["text"]
            lang = r.get("lang") or ("zhen" if re.search(r"[A-Za-z]", text) else "zh")
            o = eng.synth(text, r["spk_ref"], r.get("emo_ref"), target_s=r.get("target_s"), lang=lang)
            out = Path(r["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            sf.write(str(out), o["wav"], o["sr"], subtype="PCM_16")
            job["result"] = (200, {"id": r.get("id"), "out": str(out), "sr": o["sr"], "natural_s": o["natural_s"],
                                   "factor": o["factor"], "clamped": o["clamped"], "audio_s": o["audio_s"],
                                   "wall_s": round(time.time() - t0, 3), "queue_s": round(t0 - job["t_in"], 3),
                                   "raw_s": o.get("raw_s"), "n_codes": o.get("n_codes")})
        except Exception as e:
            traceback.print_exc()
            job["result"] = (500, {"id": r.get("id"), "error": f"{type(e).__name__}: {e}"})
        print(json.dumps({"id": r.get("id"), "code": job["result"][0], "wall_s": round(time.time() - t0, 3),
                          "abandoned": job["abandoned"]}), flush=True)
        job["done"].set()


class H(BaseHTTPRequestHandler):
    server_version = "dubsvc/1"

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0] != "/health":
            return self._send(404, {"error": "not found"})
        if STATE["error"]:
            return self._send(500, {"ok": False, "error": STATE["error"][-2000:]})
        if STATE["engine"] is None:
            return self._send(503, {"ok": False, "loading": True})
        return self._send(200, {"ok": True, **STATE["info"], "queue": JOBS.qsize()})

    def do_POST(self):
        if self.path.split("?")[0] != "/synthesize":
            return self._send(404, {"error": "not found"})
        if STATE["engine"] is None:
            return self._send(503, {"ok": False, "loading": STATE["error"] is None})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            r = json.loads(self.rfile.read(n).decode("utf-8"))
            for k in ("text", "spk_ref", "out"):
                if not r.get(k):
                    raise ValueError(f"missing {k}")
            if not Path(r["spk_ref"]).exists():
                raise ValueError(f"spk_ref not found: {r['spk_ref']}")
            if r.get("emo_ref") and not Path(r["emo_ref"]).exists():
                raise ValueError(f"emo_ref not found: {r['emo_ref']}")
            if r.get("target_s") is not None and float(r["target_s"]) <= 0:
                raise ValueError("target_s must be > 0 or null")
        except Exception as e:
            return self._send(400, {"error": f"{type(e).__name__}: {e}"})
        job = {"req": r, "done": threading.Event(), "abandoned": False, "result": None, "t_in": time.time()}
        JOBS.put(job)
        if not job["done"].wait(self.server.timeout_s):
            job["abandoned"] = True
            return self._send(504, {"id": r.get("id"), "error": f"not done within {self.server.timeout_s} s"})
        return self._send(*job["result"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8092)
    ap.add_argument("--backend", choices=["omni", "local", "voxcpm"], default="omni")
    ap.add_argument("--voxcpm-model", default="~/live-dub/models/VoxCPM2")
    ap.add_argument("--vox-steps", type=int, default=10, help="VoxCPM2 diffusion steps")
    ap.add_argument("--vox-cfg", type=float, default=2.0)
    ap.add_argument("--vox-compile", action="store_true", help="VoxCPM2 with torch.compile (10-04: no faster, +0.7G)")
    ap.add_argument("--omni-url", default="http://127.0.0.1:8093")
    ap.add_argument("--omni-wait", type=float, default=600, help="seconds to wait for vLLM-Omni to come up")
    ap.add_argument("--model", default="~/live-dub/models/IndexTTS-2.5",
                    help="IndexTTS-2.5 weights (for omni: the path vLLM-Omni serves, i.e. its model name)")
    ap.add_argument("--indextts-repo", default="~/live-dub/index-tts", help="local backend: the index-tts checkout")
    ap.add_argument("--accel", action="store_true")
    ap.add_argument("--compile", action="store_true")
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--beams", type=int, default=3)
    ap.add_argument("--max-mel-tokens", type=int, default=800)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--warmup-ref", default="", help="a short speech clip to warm the model up with (optional)")
    a = ap.parse_args()
    if a.backend == "local":   # upstream resolves HF_HUB_CACHE relative to cwd; engine.py imports from this checkout
        repo = Path(a.indextts_repo).expanduser()
        os.environ["LIVEDUB_INDEXTTS"] = str(repo)
        os.chdir(repo)
    threading.Thread(target=load, args=(a,), daemon=True).start()
    threading.Thread(target=worker, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), H)
    srv.timeout_s = a.timeout
    print(f"listening on {a.host}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
