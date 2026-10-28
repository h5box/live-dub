import json
import time
from typing import Any

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


def test_prioritize_ignores_processed_position(run):
    (run / "source.json").write_text('{"is_live": false}', encoding="utf-8")
    (run / "config.json").write_text('{"ahead": true}', encoding="utf-8")
    (run / "buf" / "list_run1.csv").write_text("seg_000000.ts,0,10\n", encoding="utf-8")
    (run / "progress.json").write_text('{"ranges": [[0.060125, 9.5]], "done": false}', encoding="utf-8")
    current = session.Session(None)
    current.dir, current.info = run, {"state": "running"}

    current.prioritize(0.060111)   # the first frame's time, a few microseconds before the range's start

    assert not (run / "priority.json").exists()


def ingest_log(d, *lines):
    (d / "ingest.log").write_text("".join(f"[18:00:0{i}] {x}\n" for i, x in enumerate(lines)), encoding="utf-8")


def test_source_error_explains_known_yt_dlp_failures(run):
    assert session.source_error(run) is None   # no ingest log yet
    cases = {"Sign in to confirm you’re not a bot. Use --cookies-from-browser": "YouTube 要求登录验证",
             "Could not copy Chrome cookie database. See  https://github.com/yt-dlp/yt-dlp/issues/7271": "浏览器正在运行",
             "Failed to decrypt with DPAPI. See  https://github.com/yt-dlp/yt-dlp/issues/10927": "无法解密",
             "[youtube] abc: Video unavailable": "无法读取这个链接：[youtube] abc: Video unavailable。每 10 秒"}
    for why, want in cases.items():
        ingest_log(run, f"resolve failed: yt-dlp gave no URL: ERROR: {why}")
        assert want in (session.source_error(run) or "")


def test_source_error_clears_once_the_stream_opens(run):
    ingest_log(run, "resolve failed: timed out", "HLS variant 1080p 5000 kb/s", "run 1: chunk 0, PT offset 0.000")
    assert session.source_error(run) is None
    ingest_log(run, "run 1: chunk 0, PT offset 0.000", "run 1 ended rc=1", "resolve failed: timed out")
    assert "timed out" in (session.source_error(run) or "")


def test_source_error_keeps_the_known_cause_when_messages_alternate(run):
    # what the real link gave behind the bot check: the two messages take turns
    ingest_log(run, "resolve failed: yt-dlp gave no URL: ERROR: [youtube] x: Sign in to confirm you’re not a bot.",
               "resolve failed: yt-dlp gave no URL: ERROR: [youtube] x: Requested format is not available.")
    assert "YouTube 要求登录验证" in (session.source_error(run) or "")


def test_source_error_goes_into_the_log_once(run):
    no_services: Any = None   # health() never asks the services
    current = session.Session(no_services)
    current.dir, current.info = run, {"name": run.name, "state": "running", "started": time.time(), "log": []}
    ingest_log(run, "resolve failed: yt-dlp gave no URL: ERROR: [youtube] x: Sign in to confirm you’re not a bot.")
    health = [current.health() for _ in range(3)][-1]   # the backend polls every 2 s
    assert "YouTube 要求登录验证" in health["source_error"]
    assert [m for _, m in current.info["log"]] == [health["source_error"]]
    ingest_log(run, "run 1: chunk 0, PT offset 0.000")
    assert current.health()["source_error"] is None


def test_source_error_follows_the_latest_known_cause(run):
    # the issue's sequence: Edge open (its cookie file locked), then closed (cookies it keeps encrypted)
    ingest_log(run, "resolve failed: yt-dlp gave no URL: ERROR: Could not copy Chrome cookie database.",
               "resolve failed: yt-dlp gave no URL: ERROR: Failed to decrypt with DPAPI.")
    assert "无法解密" in (session.source_error(run) or "")


BOT = "resolve failed: yt-dlp gave no URL: ERROR: [youtube] x: Sign in to confirm you’re not a bot."


def test_stream_not_started_reads_as_waiting(run):
    no_services: Any = None
    current = session.Session(no_services)
    current.dir, current.info = run, {"name": run.name, "state": "running", "started": time.time(), "log": []}
    # what the real link gave the day before a NASA spacewalk stream
    ingest_log(run, "resolve failed: yt-dlp gave no URL: ERROR: [youtube] hv867HnY4j4: "
                    "This live event will begin in 15 hours.")
    health = current.health()
    assert health["source_error"] == session.NOT_STARTED and health["source_waiting"]
    assert current.info["state"] == "running"
    ingest_log(run, BOT)   # anything else stays an error to act on
    assert not current.health()["source_waiting"]


@pytest.mark.parametrize("opened", [False, True])
def test_session_fails_when_ingest_gives_up_before_any_picture(run, monkeypatch, opened):
    no_services: Any = None
    current = session.Session(no_services)
    killed = []
    monkeypatch.setattr(current, "_kill_all", lambda: killed.append(1) or 0)
    current.dir, current.info = run, {"name": run.name, "state": "running", "started": time.time(), "log": []}
    before = ["run 1: chunk 0, PT offset 0.000", "run 1 ended rc=1"] if opened else []
    if opened:
        (run / "buf" / "list.csv").write_text("seg_000000.ts,0,1.0\n", encoding="utf-8")
    ingest_log(run, *before, BOT, BOT)
    assert current.health()["source_error"] and current.info["state"] == "running"   # still trying
    ingest_log(run, *before, BOT, BOT, BOT, "giving up: trying again will not get past this")
    (run / "buf" / "END").write_text("2", encoding="utf-8")
    current.health()
    assert [m for _, m in current.info["log"]] == [session.RESOLVE_HINTS[0][1]]   # the reason, once, as the last line
    if opened:   # the processor finishes what came in and exits by itself
        assert current.info["state"] == "running" and not killed
    else:        # nothing came in: the processor would wait forever; the UI offers to retry
        assert current.info["state"] == "failed" and killed
        assert json.loads((run / "run.json").read_text(encoding="utf-8"))["state"] == "failed"
