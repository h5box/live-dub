from pathlib import Path

import pytest

from livedub import doctor, settings
from livedub.server import services


def checks(monkeypatch, linux_missing):
    monkeypatch.setattr(services, "linux_missing", linux_missing)
    monkeypatch.setattr(doctor, "_gpu", dict)   # no nvidia-smi
    items = doctor.checks()
    side = next(i for i in items if i["label"].endswith(" 环境"))
    # the speech environments checked on the Linux side (ASR only where it does not run natively)
    on_side = [i for i in items if i["label"].startswith(("识别环境", "配音环境"))]
    return side, on_side


@pytest.mark.parametrize("why", [None, "Linux 环境（WSL）没有响应：WSL 未安装"])
def test_the_linux_side_shows_why_it_does_not_answer(monkeypatch, why):
    def linux_missing(paths):
        if why:
            raise RuntimeError(why)
        return []
    side, _ = checks(monkeypatch, linux_missing)
    assert side["ok"] is (why is None)
    assert side["detail"] == (why or "")   # the reason in the list, not "未找到"; a string either way
    assert bool(side["fix"]) is bool(why)


def test_what_the_linux_side_has_is_not_called_missing_when_it_does_not_answer(monkeypatch):
    def linux_missing(paths):
        raise RuntimeError("没有响应")
    side, on_side = checks(monkeypatch, linux_missing)
    assert on_side and all(not i["ok"] and not i["detail"].startswith("缺少") and side["label"] in i["detail"]
                           and side["label"] in i["fix"] and not i["install"] for i in on_side)


def test_the_linux_side_lists_what_it_lacks(monkeypatch):
    side, on_side = checks(monkeypatch, lambda paths: ["dub_py"])
    dub = next(i for i in on_side if i["label"].startswith("配音环境"))
    assert side["ok"] and dub["detail"] == "缺少：Python 环境"


def test_a_service_missing_on_the_linux_side_points_at_its_card(monkeypatch):
    # the installer ships no docs/: the error names the card of the environment page that installs it
    monkeypatch.setattr(services, "linux_missing", lambda paths: list(paths)[:1])
    monkeypatch.setitem(settings.get()["asr"], "backend", "nemo")
    svc = services.Services
    for start, card in ((lambda: svc.start_asr(svc, Path("asr.json")), "识别"), (lambda: svc.start_dub(svc), "配音")):
        with pytest.raises(RuntimeError) as e:
            start()
        assert f"「环境」页「{card}」" in str(e.value) and "docs/" not in str(e.value)
