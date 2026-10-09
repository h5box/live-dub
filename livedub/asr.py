"""Native CPU Parakeet/TitaNet service, with source-relative word timestamps."""
import json
import argparse
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import sherpa_onnx
import soundfile as sf

from . import settings

FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt", "speaker.onnx")


def words_from_tokens(tokens, timestamps, durations, audio_s):
    """SentencePiece pieces become words; punctuation remains attached for sentence splitting."""
    if len(tokens) != len(timestamps) or len(tokens) != len(durations):
        raise ValueError("识别模型没有返回完整的时间戳和时长")
    words = []
    for token, start, duration in zip(tokens, timestamps, durations):
        text = token.replace("▁", " ")
        if not text.strip():
            continue
        end = min(audio_s, start + max(0.02, duration))
        if text.startswith(" ") or not words:
            words.append({"w": text.strip(), "s": round(start, 3), "e": round(end, 3)})
        else:
            words[-1]["w"] += text
            words[-1]["e"] = round(end, 3)
    return words


class Engine:
    def __init__(self):
        a = settings.get()["asr"]
        d = settings.path(a["onnx_dir"])
        threads = max(1, min(int(a["threads"]), os.cpu_count() or 1))
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / FILES[0]), decoder=str(d / FILES[1]), joiner=str(d / FILES[2]),
            tokens=str(d / FILES[3]), num_threads=threads, model_type="nemo_transducer", provider="cpu")
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(d / FILES[4]), num_threads=threads, provider="cpu")
        self.speaker = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.lock = threading.Lock()

    def process(self, route, req):
        samples, sr = sf.read(req["wav"], dtype="float32")
        if sr != 16000 or samples.ndim != 1:
            raise ValueError("识别音频必须是 16 kHz 单声道")
        with self.lock:
            if route == "/asr":
                stream = self.recognizer.create_stream()
                stream.accept_waveform(sr, samples)
                self.recognizer.decode_stream(stream)
                r = stream.result
                return {"words": words_from_tokens(r.tokens, r.timestamps, r.durations, len(samples) / sr)}
            out = []
            for start, end in req["spans"]:
                clip = samples[max(0, int(start * sr)):min(len(samples), int(end * sr))]
                stream = self.speaker.create_stream()
                stream.accept_waveform(sr, clip)
                stream.input_finished()
                if not self.speaker.is_ready(stream):
                    raise ValueError("说话人参考音频太短")
                emb = np.asarray(self.speaker.compute(stream))
                norm = np.linalg.norm(emb)
                if norm == 0:
                    raise ValueError("说话人声纹为空")
                out.append((emb / norm).tolist())
            return {"emb": out}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=settings.port("asr"))
    args = parser.parse_args()
    engine = Engine()

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(200 if self.path == "/health" else 404,
                       {"ok": True, "backend": "onnx", "boost": []})

        def do_POST(self):
            if self.path not in ("/asr", "/embed"):
                self.reply(404, {"error": "接口不存在"})
                return
            try:
                t = time.monotonic()
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                self.reply(200, {**engine.process(self.path, req), "wall_s": round(time.monotonic() - t, 3)})
            except Exception as e:
                self.reply(500, {"error": str(e)})

        def log_message(self, format, *args):
            pass

    print("原生 CPU 识别已就绪（ONNX；暂不支持识别热词增强）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
