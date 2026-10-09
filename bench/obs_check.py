"""OBS pull check: in a separate scene collection, add a Media Source on the playout's RTMP stream (via MediaMTX),
then take screenshots and read the media state for a while. Never starts streaming or recording, and switches back
to the scene collection that was active before.

Needs OBS running with obs-websocket enabled (the password is read from OBS's own config, never printed).
usage: uv run --with obsws-python python bench/obs_check.py rtmp://127.0.0.1:1935/live/live1 OUT_DIR [--secs 120]
"""
import argparse
import base64
import json
import os
import time
from pathlib import Path

import obsws_python as obs

COLL, SCENE, INPUT = "livedub-test", "live-dub", "live-dub stream"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("out"); ap.add_argument("--secs", type=int, default=120)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((Path(os.environ["APPDATA"]) / "obs-studio/plugin_config/obs-websocket/config.json").read_text())
    for _ in range(60):
        try:
            cl = obs.ReqClient(host="127.0.0.1", port=cfg.get("server_port", 4455),
                               password=cfg.get("server_password", ""), timeout=10)
            break
        except Exception:
            time.sleep(2)
    else:
        raise SystemExit("obs-websocket not reachable")
    print("OBS", cl.get_version().obs_version, flush=True)
    before = cl.get_scene_collection_list()
    orig = before.current_scene_collection_name
    print("current collection:", orig, flush=True)
    try:
        if COLL in before.scene_collections:
            cl.set_current_scene_collection(COLL)
        else:
            cl.create_scene_collection(COLL)
        time.sleep(3)
        scenes = [s["sceneName"] for s in cl.get_scene_list().scenes]
        if SCENE not in scenes:
            cl.create_scene(SCENE)
        cl.set_current_program_scene(SCENE)
        inputs = [i["inputName"] for i in cl.get_input_list().inputs]
        settings = {"is_local_file": False, "input": a.url, "input_format": "", "restart_on_activate": True,
                    "reconnect_delay_sec": 2, "buffering_mb": 2, "hw_decode": True, "close_when_inactive": False}
        if INPUT in inputs:
            cl.set_input_settings(INPUT, settings, True)
        else:
            cl.create_input(SCENE, INPUT, "ffmpeg_source", settings, True)
        sid = cl.get_scene_item_id(SCENE, INPUT).scene_item_id
        cl.set_scene_item_transform(SCENE, sid, {"boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 1920,
                                                 "boundsHeight": 1080, "positionX": 0, "positionY": 0})
        rows = []
        t0 = time.time()
        k = 0
        while time.time() - t0 < a.secs:
            time.sleep(10)
            st = cl.get_media_input_status(INPUT)
            stats = cl.get_stats()
            row = {"t": round(time.time() - t0, 1), "state": st.media_state, "cursor_ms": st.media_cursor,
                   "fps": round(stats.active_fps, 2), "render_skipped": stats.render_skipped_frames,
                   "output_skipped": stats.output_skipped_frames}
            if k % 3 == 0:
                shot = cl.get_source_screenshot(INPUT, "png", 960, 540, -1).image_data
                p = out / f"obs_{k:03d}.png"
                p.write_bytes(base64.b64decode(shot.split(",", 1)[1]))
                row["shot"] = p.name
            rows.append(row); k += 1
            print(json.dumps(row), flush=True)
        (out / "obs_check.json").write_text(json.dumps(rows, indent=1))
    finally:
        cl.set_current_scene_collection(orig)
        print("switched back to", orig, flush=True)


if __name__ == "__main__":
    main()
