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
