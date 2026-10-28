import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from livedub.server import app


class FakeServices:
    def __init__(self):
        self.calls = []

    async def stop(self, name):
        self.calls.append(("stop", name))

    async def wait_down(self, name, timeout):
        return True

    async def start(self, name, **kw):
        self.calls.append(("start", name, kw))


@pytest.fixture
def services(monkeypatch):
    fake = FakeServices()
    monkeypatch.setattr(app, "services", fake)
    monkeypatch.setattr(app, "session", SimpleNamespace(active=False))
    return fake


def call(name, action, **body):
    return asyncio.run(app.service(name, action, app.SvcReq(**body)))


def test_restart_with_a_preset_passes_its_file(services):
    assert call("asr", "restart", config="default") == {"ok": True}
    assert services.calls == [("stop", "asr"), ("start", "asr", {"config": app.sess.preset_file("default")})]


def test_unknown_preset_is_refused_before_anything_stops(services):
    with pytest.raises(HTTPException) as e:
        call("asr", "restart", config="no-such-preset")
    assert e.value.status_code == 404
    assert services.calls == []


def test_services_cannot_be_stopped_during_a_session(services, monkeypatch):
    monkeypatch.setattr(app, "session", SimpleNamespace(active=True))
    with pytest.raises(HTTPException) as e:
        call("asr", "stop")
    assert e.value.status_code == 409
    assert call("asr", "start") == {"ok": True}


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setenv("LIVEDUB_SETTINGS", str(f))
    app.settings.reload()
    yield f
    monkeypatch.delenv("LIVEDUB_SETTINGS")
    app.settings.reload()


def youtube(**body):
    return asyncio.run(app.youtube_set(app.YoutubeReq(**body)))


def test_youtube_sign_in_is_saved_and_checked(settings_file, tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    assert youtube(cookies=f'"{cookies}"') == {"cookies": str(cookies), "cookies_from_browser": "", "cookies_found": True}
    assert app.settings.get()["youtube"]["cookies"] == str(cookies)   # the next session reads it without a restart
    assert youtube(cookies_from_browser=" Firefox:work ")["cookies_from_browser"] == "Firefox:work"   # a profile too
    assert app.settings.get()["youtube"] == {"cookies": "", "cookies_from_browser": "Firefox:work"}
    assert "ports" not in settings_file.read_text(encoding="utf-8")   # only what was set goes into the file


@pytest.mark.parametrize("body, why", [({"cookies": "missing/cookies.txt"}, "找不到 cookies 文件"),
                                       ({"cookies_from_browser": "netscape:firefox"}, "不支持从这个浏览器读取")])
def test_youtube_sign_in_rejects_what_yt_dlp_cannot_use(settings_file, body, why):
    with pytest.raises(HTTPException) as e:
        youtube(**body)
    assert e.value.status_code == 400 and why in e.value.detail
    assert not settings_file.exists()


def test_status_says_which_version_is_running(monkeypatch):
    monkeypatch.setattr(app.services, "status", {"services": {}})
    monkeypatch.setattr(app, "session", SimpleNamespace(info=None, active=False))
    assert app.snapshot()["version"] == app.__version__


@pytest.mark.parametrize("background, stopped", [(False, ["mt", "dub"]), (True, [])])
def test_closing_the_window_stops_the_services_once_no_session_runs(services, settings_file, monkeypatch,
                                                                     background, stopped):
    app.settings.save({"run_in_background": background})
    services.status = {"services": {"asr": {"up": False}, "mt": {"up": True},
                                    "dub": {"up": False, "pending": "starting"}, "mediamtx": {"up": False}}}
    asyncio.run(app.closed())
    monkeypatch.setattr(app.session, "active", True)
    asyncio.run(app.after_window_closed())
    assert services.calls == []                     # the session goes on
    monkeypatch.setattr(app.session, "active", False)
    asyncio.run(app.after_window_closed())
    asyncio.run(app.after_window_closed())          # once
    assert services.calls == [("stop", n) for n in stopped]
    assert not app.window_closed
