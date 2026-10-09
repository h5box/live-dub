"""python -m livedub.server [--open]  ->  http://127.0.0.1:8090 (loopback only; --open shows the UI in a browser)."""
import argparse
import threading
import webbrowser

import uvicorn

from .app import app

PORT = 8090   # fixed: the desktop app and its content security policy expect it

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="open the UI in the default browser (needs app/dist built)")
    a = ap.parse_args()
    if a.open:
        threading.Timer(1.5, webbrowser.open, args=(f"http://127.0.0.1:{PORT}/",)).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
