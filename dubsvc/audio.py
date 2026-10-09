"""Audio helpers shared by the dubbing backends (numpy only)."""
import numpy as np

SR = 22050


def trim_silence(wav, sr=SR, keep_ms=30, tail_ms=30, floor_db=40.0):
    """wav: float32 1-D in [-1, 1]. Cut leading/trailing 10 ms frames quieter than peak - floor_db."""
    hop = sr // 100
    n = len(wav) // hop
    if n == 0:
        return wav, 0.0
    fr = wav[: n * hop].reshape(n, hop)
    db = 20 * np.log10(np.sqrt((fr.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)
    loud = np.nonzero(db > db.max() - floor_db)[0]
    if len(loud) == 0:
        return wav, 0.0
    s = max(0, loud[0] * hop - int(sr * keep_ms / 1000))
    e = min(len(wav), (loud[-1] + 1) * hop + int(sr * tail_ms / 1000))
    return wav[s:e], s / sr
