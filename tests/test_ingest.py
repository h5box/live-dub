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


# yt-dlp's message for this, verbatim: the reason comes first and its help text would fill a 300-character tail
BOT_CHECK = ("ERROR: [youtube] QB5BNdBFujE: Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies "
             "for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for "
             "how to manually pass cookies. Also see  https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-"
             "cookies  for tips on effectively exporting YouTube cookies")


def test_resolve_failure_keeps_the_reason(monkeypatch):
    done = ingest.subprocess.CompletedProcess([], 1, stdout="", stderr="[youtube] Extracting URL\n" + BOT_CHECK + "\n")
    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **k: done)
    try:
        ingest.resolve("https://www.youtube.com/watch?v=QB5BNdBFujE", None)
    except RuntimeError as e:
        assert "Sign in to confirm" in str(e)
    else:
        raise AssertionError("no URL should raise")
