import json
import threading

from livedub import ingest


def test_fast_recording_still_writes_anchor(tmp_path, monkeypatch):
    buf = tmp_path / "buf"
    buf.mkdir()
    (buf / "list.csv").write_text("seg_000000.ts,0,1.2\n", encoding="utf-8")
    monkeypatch.setattr(ingest, "first_pt", lambda _: 0.04)
    stop = threading.Event()
    stop.set()
    anchor = tmp_path / "anchor.json"

    ingest.anchor_when_first_chunk(buf, anchor, 30, stop)

    result = json.loads(anchor.read_text(encoding="utf-8"))
    assert result["p0"] == 0.04
    assert result["delay"] == 30


def test_empty_recording_does_not_wait_after_exit(tmp_path):
    stop = threading.Event()
    stop.set()
    ingest.anchor_when_first_chunk(tmp_path, tmp_path / "anchor.json", 30, stop)
    assert not (tmp_path / "anchor.json").exists()
