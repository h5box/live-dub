"""Turn the offline M1 results (translation units + TTS clips) into a playout events JSONL on the source timeline.

Segment B starts 1440 s into the DevDay recording, so PT = 1440 + unit time.
usage: python bench/make_test_events.py MT_JSON TTS_RESULT_JSON OUT_JSONL [--seg-start 1440]
"""
import argparse
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("mt"); ap.add_argument("tts"); ap.add_argument("out"); ap.add_argument("--seg-start", type=float, default=1440.0)
a = ap.parse_args()
mt = json.loads(Path(a.mt).read_text(encoding="utf-8"))
tts = {r["i"]: r for r in json.loads(Path(a.tts).read_text(encoding="utf-8"))["rows"]}
d = Path(a.tts).parent.resolve()
with open(a.out, "w", encoding="utf-8") as f:
    for i, (u, r) in enumerate(zip(mt["sentences"], mt["runs"]["q4"]["rows"])):
        t = tts.get(i, {})
        dub = None
        if "nat_s" in t:
            dub = str(d / f"{i:02d}_{'dur' if t.get('use') == 'dur' and 'dur_s' in t else 'nat'}.wav")
        f.write(json.dumps({"pt0": round(a.seg_start + u["s"], 3), "pt1": round(a.seg_start + u["e"], 3),
                            "zh": r["zh"], "en": u["en"], "dub": dub}, ensure_ascii=False) + "\n")
print("wrote", a.out)
