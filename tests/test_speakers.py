import numpy as np

from livedub.processor import Speakers


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def test_nearest_speaker_with_a_reference():
    spk = Speakers(0.9)
    a = spk.assign(unit([1, 0, 0]), 4.0)          # speaker with a long clip
    b = spk.assign(unit([0, 1, 0]), 4.0)          # another one
    c = spk.assign(unit([0.6, 0.3, 0.74]), 1.0)   # a short line too far from both: a cluster of its own
    assert len({a, b, c}) == 3
    assert spk.nearest(c, lambda j: j in (a, b)) == a
    assert spk.nearest(c, lambda j: j == b) == b
    assert spk.nearest(c, lambda j: False) is None
    assert spk.nearest(None, lambda j: True) is None
