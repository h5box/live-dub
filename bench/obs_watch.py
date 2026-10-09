"""Put the playout stream on screen in OBS for a person to watch: switch to the separate "livedub-test" scene
collection, point its Media Source at the RTMP URL, turn on audio monitoring (so the dub is audible locally) and
leave it there. Never starts streaming or recording and never touches other scene collections; switch back in
OBS (Scene Collection menu) when done. Password is read from OBS's own config, never printed.
usage: uv run --with obsws-python python bench/obs_watch.py rtmp://127.0.0.1:1935/live/live2
"""
import json
import os
import sys
import time
from pathlib import Path

import obsws_python as obs

COLL, SCENE, INPUT = "livedub-test", "live-dub", "live-dub stream"


def main():
    url = sys.argv[1]
    cfg = json.loads((Path(os.environ["APPDATA"]) / "obs-studio/plugin_config/obs-websocket/config.json").read_text())
    cl = obs.ReqClient(host="127.0.0.1", port=cfg.get("server_port", 4455), password=cfg.get("server_password", ""),
                       timeout=10)
    colls = cl.get_scene_collection_list()
    if colls.current_scene_collection_name != COLL:
        if COLL in colls.scene_collections:
            cl.set_current_scene_collection(COLL)
        else:
            cl.create_scene_collection(COLL)
        time.sleep(3)
    if SCENE not in [s["sceneName"] for s in cl.get_scene_list().scenes]:
        cl.create_scene(SCENE)
    cl.set_current_program_scene(SCENE)
    settings = {"is_local_file": False, "input": url, "input_format": "", "restart_on_activate": True,
                "reconnect_delay_sec": 2, "buffering_mb": 2, "hw_decode": True, "close_when_inactive": False}
    if INPUT in [i["inputName"] for i in cl.get_input_list().inputs]:
        cl.set_input_settings(INPUT, settings, True)
    else:
        cl.create_input(SCENE, INPUT, "ffmpeg_source", settings, True)
    sid = cl.get_scene_item_id(SCENE, INPUT).scene_item_id
    cl.set_scene_item_transform(SCENE, sid, {"boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 1920,
                                             "boundsHeight": 1080, "positionX": 0, "positionY": 0})
    cl.set_input_audio_monitor_type(INPUT, "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT")
    cl.trigger_media_input_action(INPUT, "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART")
    time.sleep(5)
    st = cl.get_media_input_status(INPUT)
    print("collection", COLL, "| source", url, "| state", st.media_state,
          "| streaming", cl.get_stream_status().output_active, "| recording", cl.get_record_status().output_active)


if __name__ == "__main__":
    main()
