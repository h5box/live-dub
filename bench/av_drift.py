"""A/V alignment of a recording against its source file, at several points of the recording.

For each probe time t in the recording, finds where that stretch sits in the source: audio by cross-correlating
16 kHz mono (the ducked original is still in the mix under the dubs), video by matching a run of 64x36 grey
thumbnails. Reports both source offsets and their difference (a/v skew); a skew or offset that changes between the
first and the last probe is drift.

usage: python bench/av_drift.py REC.mp4 SOURCE.mkv --guess SRC_MINUS_REC_SECONDS [--probes 60,600,...] [--search 20]
"""
import argparse

import av
import numpy as np

SR = 16000


def audio(path, t0, dur):
    with av.open(path) as c:
        c.seek(int(max(t0 - 1, 0) * 1e6))
        rs = av.AudioResampler(format="flt", layout="mono", rate=SR)
        parts, start = [], None
        for fr in c.decode(audio=0):
            if fr.pts is None:
                continue
            t = float(fr.pts * fr.time_base)
            for f in rs.resample(fr):
                if start is None:
                    start = t
                parts.append(f.to_ndarray().reshape(-1))
            if t > t0 + dur + 1:
                break
    x = np.concatenate(parts)
    a = int(round((t0 - start) * SR))
    return x[max(a, 0):max(a, 0) + int(dur * SR)]


def thumbs(path, t0, dur):
    out, ts = [], []
    with av.open(path) as c:
        c.seek(int(max(t0 - 2, 0) * 1e6))
        for fr in c.decode(video=0):
            if fr.pts is None:
                continue
            t = float(fr.pts * fr.time_base)
            if t < t0:
                continue
            if t > t0 + dur:
                break
            g = fr.reformat(width=64, height=36, format="gray").to_ndarray().astype(np.float32)
            out.append(g - g.mean()); ts.append(t)
    return np.array(ts), np.stack(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rec"); ap.add_argument("src")
    ap.add_argument("--guess", type=float, required=True, help="source time minus recording time, roughly")
    ap.add_argument("--probes", default="60,600,1200,1800,2400,3000")
    ap.add_argument("--search", type=float, default=20.0)
    a = ap.parse_args()
    W = 8.0
    for t in [float(x) for x in a.probes.split(",")]:
        try:
            r = audio(a.rec, t, W)
            s = audio(a.src, t + a.guess - a.search, W + 2 * a.search)
            f = np.fft.rfft; n = len(s) + len(r)
            cc = np.fft.irfft(f(s, n) * np.conj(f(r, n)), n)[: len(s) - len(r)]
            c2 = np.concatenate([[0.0], np.cumsum(s.astype(np.float64) ** 2)]); norm = np.sqrt((c2[len(r):] - c2[:-len(r)])[: len(cc)] * (r ** 2).sum()) + 1e-9
            k = int(np.argmax(cc / norm)); a_off = t + a.guess - a.search + k / SR - t
            rt, rv = thumbs(a.rec, t, 3.0)
            st, sv = thumbs(a.src, t + a_off - 2.0, 7.0)
            best = None
            for j in range(len(sv) - len(rv)):
                e = float(((sv[j:j + len(rv)] - rv) ** 2).mean())
                if best is None or e < best[0]:
                    best = (e, j)
            v_off = st[best[1]] - rt[0]
            print(f"rec {t:7.1f}s  audio offset {a_off:9.3f}  (corr {float((cc / norm)[k]):.2f})  video offset {v_off:9.3f}"
                  f"  a/v skew {1000 * (a_off - v_off):+6.0f} ms", flush=True)
        except Exception as ex:
            print(f"rec {t:7.1f}s  failed: {ex!r}", flush=True)


if __name__ == "__main__":
    main()
