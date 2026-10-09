"""Process helpers: start detached, find and kill by command line (Windows and Linux).

Session processes and services are started detached (their own process group, no console window, output to a log),
so they outlive the backend: a live session keeps going while the window or the backend restarts, and a restarted
backend finds them again by command line.
"""
import os
import re
import subprocess
from pathlib import Path

import psutil

WINDOWS = os.name == "nt"


def spawn(argv: list[str], log: Path, err: Path | None = None, cwd: str | Path | None = None,
          env: dict | None = None) -> int:
    """Start argv detached with stdout to `log` (stderr to `err`, or the same file); returns its PID."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    out = open(log, "ab")
    er = open(err, "ab") if err else subprocess.STDOUT
    full_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", **(env or {})}
    kw = dict(cwd=str(cwd) if cwd else None, env=full_env, stdin=subprocess.DEVNULL, stdout=out, stderr=er)
    try:
        if WINDOWS:
            # no console window (children such as ffmpeg inherit the hidden console), not in our Ctrl+C group, and
            # out of our job if we are in one that allows it (an SSH session's job would otherwise take it down)
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            try:
                p = subprocess.Popen(argv, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
            except OSError:
                p = subprocess.Popen(argv, creationflags=flags, **kw)
        else:
            p = subprocess.Popen(argv, start_new_session=True, **kw)
    finally:
        out.close()
        if err:
            er.close()
    return p.pid


def _base(name: str) -> str:
    """python3.12.exe -> python: image names compared without extension and version."""
    n = (name or "").lower()
    n = n[:-4] if n.endswith(".exe") else n
    return re.sub(r"[\d.]+$", "", n)


def find(pattern: str, names: tuple[str, ...] = ()) -> list[psutil.Process]:
    """Processes whose command line matches the regex (optionally only these image names, e.g. "python")."""
    rx = re.compile(pattern, re.I)
    want = {_base(n) for n in names}
    hits = []
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            if want and _base(p.info["name"]) not in want:
                continue
            cl = " ".join(p.info["cmdline"] or [])
            if cl and rx.search(cl):
                hits.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return hits


def kill(procs: list[psutil.Process], tree: bool = False, spare: tuple[str, ...] = ("llama-server",)) -> int:
    """Kill these processes (and with tree=True their descendants, except the image names in `spare`)."""
    procs = list(procs)
    spare_b = {_base(s) for s in spare}
    if tree:
        for p in list(procs):
            try:
                procs += [c for c in p.children(recursive=True) if c not in procs and _base(c.name()) not in spare_b]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    n = 0
    for p in procs:
        try:
            p.kill(); n += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    psutil.wait_procs(procs, timeout=5)
    return n


def alive(pid: int | None) -> bool:
    try:
        return bool(pid) and psutil.Process(pid).is_running()
    except psutil.NoSuchProcess:
        return False
