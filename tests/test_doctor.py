import pytest

from livedub import doctor
from livedub.server import services


@pytest.mark.parametrize("why", [None, "Linux 环境（WSL）没有响应：WSL 未安装"])
def test_the_linux_side_shows_why_it_does_not_answer(monkeypatch, why):
    def linux_missing(paths):
        if why:
            raise RuntimeError(why)
        return []
    monkeypatch.setattr(services, "linux_missing", linux_missing)
    monkeypatch.setattr(doctor, "_gpu", dict)   # no nvidia-smi
    side = next(i for i in doctor.checks() if i["label"].endswith(" 环境"))
    assert side["ok"] is (why is None)
    assert side["detail"] == (why or "")   # the reason in the list, not "未找到"; a string either way
    assert bool(side["fix"]) is bool(why)
