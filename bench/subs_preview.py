"""Preview subtitle placement for a stream config on a real frame: grabs one frame from a video/chunk and draws a
two-line subtitle, the tag and nothing else, with the config's sub_bottom / sub_box / tag_corner.
usage: uv run python bench/subs_preview.py CONFIG FRAME_SOURCE OUT.png [--at 1]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livedub import subs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config"); ap.add_argument("src"); ap.add_argument("out"); ap.add_argument("--at", default="1")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", a.at, "-i", a.src, "-frames:v", "1", "-vf", "scale=1920:1080",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    rgb = np.frombuffer(raw, np.uint8).reshape(1080, 1920, 3).copy()
    subs.configure(cfg.get("sub_bottom"), cfg.get("sub_box"), cfg.get("tag_corner"))
    subs.blend(rgb, subs.render("首相表示，政府将在下周公布新的住房计划，重点是首次购房者",
                                "The Prime Minister said the government will publish a new housing plan next week."))
    subs.blend(rgb, subs.render_tag(cfg.get("tag") or "AI 翻译配音"))
    Image.fromarray(rgb).save(a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()
