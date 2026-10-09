"""Leading silence of the dub clips actually used in the M1 demo: if IndexTTS starts every clip with a gap,
the dub lands late by that much even though it is placed exactly at the sentence onset.
First 10 ms frame whose RMS is within 30 dB of the clip's loudest frame counts as the voice onset.

usage: python lead_silence.py PLACED_JSON TTS_DIR
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

placed = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["dubs"]
lead = []
for p in placed:
    with wave.open(str(Path(sys.argv[2]) / p["file"])) as w:
        sr = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop = sr // 100
    frames = x[: len(x) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    onset = int(np.argmax(db > db.max() - 30)) * 0.01
    lead.append((onset, p["i"]))
lead.sort()
v = [x for x, _ in lead]
print(f"n={len(v)} lead silence ms: min {v[0]*1000:.0f}  p50 {v[len(v)//2]*1000:.0f}  p90 {v[int(len(v)*0.9)]*1000:.0f}  max {v[-1]*1000:.0f}")
print("longest:", [(i, round(s * 1000)) for s, i in lead[-5:]])
