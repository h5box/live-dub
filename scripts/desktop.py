"""Frozen backend entry: dispatch only the application's known worker modules."""
import io
import multiprocessing
import runpy
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # a frozen build ignores the PYTHONIOENCODING and PYTHONUNBUFFERED that procs.spawn and the desktop shell set:
    # without this, yt-dlp and the workers write the system code page (GBK on Chinese Windows), ingest reads its
    # output as UTF-8, and printing the result back to a GBK stdout fails on the replacement characters
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):   # None when the process has no console or pipe
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    modules = {"livedub.server", "livedub.asr", "livedub.ingest", "livedub.processor", "livedub.playout",
               "yt_dlp", "streamlink"}
    module = sys.argv.pop(1) if len(sys.argv) > 1 else "livedub.server"
    if module not in modules:
        sys.exit("未知的声影同传工作进程")
    runpy.run_module(module, run_name="__main__")
