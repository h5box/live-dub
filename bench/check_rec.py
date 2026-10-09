"""Check a playout recording against the source: find where rec t=0 sits on the source timeline (audio
cross-correlation), then cut frames just before/after a subtitle event's onset and measure dub energy.

usage: python bench/check_rec.py REC.mp4 SRC_48K.wav SEG_START EVENTS_JSONL OUT_DIR [--event N]
"""
import argparse
import json
from pathlib import Path

import av
import numpy as np


def load(path, sr=8000, seconds=None, mono=True):
    with av.open(str(path)) as c:
        rs = av.AudioResampler(format="flt", layout="mono" if mono else "stereo", rate=sr)
        out, n = [], 0
        for fr in c.decode(audio=0):
            for f in rs.resample(fr):
                x = f.to_ndarray().reshape(-1); out.append(x); n += len(x)
            if seconds and n >= seconds * sr:
                break
    return np.concatenate(out)


ap = argparse.ArgumentParser()
ap.add_argument("rec"); ap.add_argument("src"); ap.add_argument("seg_start", type=float)
ap.add_argument("events"); ap.add_argument("out"); ap.add_argument("--event", type=int, default=10)
a = ap.parse_args()
sr = 8000
rec = load(a.rec, sr, seconds=40)
src = load(a.src, sr, seconds=120)
# cross-correlate 20 s of the recording against the source (FFT)
r = rec[: 20 * sr] - rec[: 20 * sr].mean()
s = src - src.mean()
n = 1 << int(np.ceil(np.log2(len(s) + len(r))))
corr = np.fft.irfft(np.fft.rfft(s, n) * np.conj(np.fft.rfft(r, n)), n)[: len(s) - len(r)]
lag = int(np.argmax(corr)) / sr
print(f"rec t=0  <->  segment t={lag:.3f} s  (PT {a.seg_start + lag:.3f}); peak/median {corr.max() / np.median(np.abs(corr)):.0f}")

evs = [json.loads(l) for l in open(a.events, encoding="utf-8")]
e = evs[a.event]
t_on = e["pt0"] - a.seg_start - lag  # onset on the recording's timeline
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
with av.open(a.rec) as c:
    vs = c.streams.video[0]
    frames = {}
    c.seek(int(max(0, t_on - 2) / vs.time_base), stream=vs)
    for fr in c.decode(vs):
        t = float(fr.pts * fr.time_base)
        if t_on - 0.2 <= t <= t_on + 0.2:
            frames[round(t - t_on, 3)] = fr.to_image()
        if t > t_on + 0.25:
            break
keys = sorted(frames)
before = max(k for k in keys if k < 0); after = min(k for k in keys if k >= 0)
for k, name in ((before, "before"), (after, "after")):
    frames[k].resize((960, 540)).save(out / f"onset_{name}.png")
    # subtitle band brightness (bottom 20% of the frame) as a crude on/off signal
    band = np.asarray(frames[k])[860:1040].astype(np.float32)
    print(f"frame {k * 1000:+.0f} ms from onset: bright pixels in subtitle band {(band.min(axis=2) > 235).mean() * 100:.2f}%")
print(f"event {a.event}: PT {e['pt0']:.3f}-{e['pt1']:.3f} '{e['zh']}' -> recording {t_on:.3f} s")

# dub energy: recording vs source in the event window (source there is ducked by 15 dB)
rec_full = load(a.rec, sr)
seg = lambda x, t0, t1: x[int(t0 * sr): int(t1 * sr)]
i0, i1 = t_on + 0.1, t_on + min(2.0, e["pt1"] - e["pt0"])
er = float(np.sqrt(np.mean(seg(rec_full, i0, i1) ** 2)))
es = float(np.sqrt(np.mean(seg(src, e["pt0"] - a.seg_start + 0.1, e["pt0"] - a.seg_start + (i1 - t_on)) ** 2)))
print(f"RMS in the first 2 s of the event: recording {20 * np.log10(er + 1e-9):.1f} dBFS, source {20 * np.log10(es + 1e-9):.1f} dBFS")
