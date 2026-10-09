"""Frozen backend entry: dispatch only the application's known worker modules."""
import multiprocessing
import runpy
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    modules = {"livedub.server", "livedub.asr", "livedub.ingest", "livedub.processor", "livedub.playout",
               "yt_dlp", "streamlink"}
    module = sys.argv.pop(1) if len(sys.argv) > 1 else "livedub.server"
    if module not in modules:
        sys.exit("未知的 LiveDub 工作进程")
    runpy.run_module(module, run_name="__main__")
