import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from livedub import settings
from livedub.server import app


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setenv("LIVEDUB_SETTINGS", str(f))
    yield f
    monkeypatch.delenv("LIVEDUB_SETTINGS")
    settings.reload()


def test_a_broken_settings_file_falls_back_to_the_defaults_and_says_where(settings_file):
    broken = '{\n  "github_mirror": "https://example.com/"\n  "keep_media_runs": 3\n}\n'   # a comma left out
    settings_file.write_text(broken, encoding="utf-8")
    s = settings.reload()   # used to raise, and the backend did not start
    assert s["keep_media_runs"] == settings.DEFAULTS["keep_media_runs"]
    assert "第 3 行第 3 列附近" in settings.problem and str(settings_file) in settings.problem
    with pytest.raises(settings.Invalid):
        settings.save({"keep_media_runs": 1})
    assert settings_file.read_text(encoding="utf-8") == broken   # what its owner wrote is kept


def test_the_ui_hears_of_it(settings_file, monkeypatch):
    settings_file.write_text("[]", encoding="utf-8")
    settings.reload()
    assert "最外层需要是 { }" in settings.problem
    monkeypatch.setattr(app, "session", SimpleNamespace(active=False, info=None))
    monkeypatch.setattr(app, "services", SimpleNamespace(status={"warnings": ["显存不足"]}))
    assert app.snapshot()["warnings"] == [settings.problem, "显存不足"]
    assert app.app.exception_handlers[settings.Invalid] is app._settings_invalid
    no_request: Any = None
    refused = asyncio.run(app._settings_invalid(no_request, settings.Invalid("x")))
    assert refused.status_code == 409 and "改正并重启后端后才能保存设置" in json.loads(bytes(refused.body))["detail"]


def test_a_file_saved_with_a_bom_is_read(settings_file):
    settings_file.write_text('{"keep_media_runs": 2}', encoding="utf-8-sig")   # Notepad, PowerShell 5
    assert settings.reload()["keep_media_runs"] == 2 and not settings.problem


@pytest.mark.parametrize("text, where", [
    ('{"linux": "~/live-dub"}', 'linux 需要是 { }，不能是 "~/live-dub"'),
    ('{"asr": null}', "asr 需要是 { }，不能是 null"),
    ('{"mt": {"models": []}}', "mt.models 需要是 { }，不能是 []"),
])
def test_a_group_that_is_not_an_object_falls_back_to_the_defaults(settings_file, text, where):
    settings_file.write_text(text, encoding="utf-8")
    s = settings.reload()   # used to raise TypeError or AttributeError, and the backend did not start
    assert s["linux"]["home"] == settings.DEFAULTS["linux"]["home"]
    assert where in settings.problem and "少了逗号" not in settings.problem
    with pytest.raises(settings.Invalid):
        settings.save({"keep_media_runs": 1})
    assert settings_file.read_text(encoding="utf-8") == text


def test_groups_that_add_their_own_keys_stay_allowed(settings_file):
    settings_file.write_text('{"mt": {"models": {"x": {"path": "D:/x.gguf"}}}, "online": {"deepseek": {}}}',
                             encoding="utf-8")
    assert settings.reload()["mt"]["models"]["x"]["path"] == "D:/x.gguf" and not settings.problem
