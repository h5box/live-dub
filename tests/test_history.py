import json

import pytest

from livedub.server import session


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "RUNS", tmp_path)
    d = tmp_path / "20261010-test"
    (d / "buf").mkdir(parents=True)
    (d / "clips").mkdir()
    (d / "run.json").write_text(json.dumps({"name": d.name, "state": "ended"}), encoding="utf-8")
    (d / "buf" / "seg_000000.ts").write_bytes(b"test")
    (d / "clips" / "dub_00001.wav").write_bytes(b"test")
    (d / "anchor.json").write_text(json.dumps({"p0": 12.5}), encoding="utf-8")
    event = {"pt0": 13.5, "pt1": 15, "zh": "你好", "en": "Hello"}
    (d / "events.jsonl").write_text(json.dumps(event) + "\n" + '{"pt0":', encoding="utf-8")
    return d


def test_export_uses_source_time_and_ignores_partial_event(run):
    assert session.subtitles(run.name, "both") == "1\n00:00:01,000 --> 00:00:02,500\n你好\nHello\n"


def test_cache_cleanup_keeps_transcript(run):
    session.remove_run(run.name, cache_only=True)
    detail = session.run_detail(run.name)
    assert detail["info"]["media"] is None
    assert len(detail["events"]) == 1
    assert not (run / "clips").exists()


@pytest.mark.parametrize("name", ["../other", "..", "/outside", "test/../other"])
def test_rejects_paths_outside_history(run, name):
    with pytest.raises(ValueError):
        session.remove_run(name)
    assert run.exists()


def test_active_run_cannot_be_deleted(run):
    (run / "run.json").write_text(json.dumps({"name": run.name, "state": "running"}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        session.remove_run(run.name)
    assert (run / "buf").exists()


def test_full_delete_is_limited_to_selected_run(run):
    sibling = run.parent / "untouched"
    sibling.mkdir()
    session.remove_run(run.name)
    assert not run.exists()
    assert sibling.exists()


def test_url_recording_can_prioritize_before_ingest_finishes(run):
    (run / "source.json").write_text('{"is_live": false}', encoding="utf-8")
    (run / "config.json").write_text('{"ahead": true}', encoding="utf-8")
    (run / "buf" / "list_run1.csv").write_text("seg_000000.ts,0,10\n", encoding="utf-8")
    current = session.Session(None)
    current.dir, current.info = run, {"state": "running"}

    current.prioritize(8)

    request = json.loads((run / "priority.json").read_text(encoding="utf-8"))
    assert request["segment"] == 0
    assert request["pt"] == 8
