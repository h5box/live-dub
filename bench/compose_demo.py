"""M1 offline demo: burn bilingual subtitles and mix the cloned Chinese dub over the ducked original.

Everything is placed on the source timeline: subtitle i shows over [s_i, e_i] (ASR word timestamps), dub i
starts at s_i. Original audio is ducked as a whole (B0 in docs/dev/选型.md, no vocal separation yet): -15 dB,
250 ms raised-cosine ramp finishing at the dub onset, 1 s release, kept down across gaps < 1.5 s.
Windows side: ffmpeg (subtitles filter + h264_nvenc) and numpy (uv run --with numpy).

usage: python compose_demo.py --mt MT_JSON --tts TTS_RESULT_JSON --video MKV --start 1440 --wav48 B.48k.wav --out OUT_DIR
"""
import argparse
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 48000
DUCK_DB, ATTACK, RELEASE, BRIDGE = -15.0, 0.25, 1.0, 1.5
ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ZH,Microsoft YaHei,58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,80,80,40,134
Style: EN,Arial,36,&H00E0E0E0,&H00E0E0E0,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,80,80,44,1
Style: TAG,Microsoft YaHei,26,&H40FFFFFF,&H40FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,7,36,36,30,134

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


ZH_LINE = 24  # 58 px YaHei on 1920 with 80 px margins fits ~28; libass does not break Chinese (no spaces)


def wrap_zh(t):
    """Break a Chinese line at the punctuation nearest the middle (else at the middle), at most two lines."""
    if len(t) <= ZH_LINE:
        return t
    mid = len(t) / 2
    cuts = [i + 1 for i, c in enumerate(t) if c in "，、；：,;" and 0 < i + 1 < len(t)]
    cut = min(cuts, key=lambda i: abs(i - mid)) if cuts and abs(min(cuts, key=lambda i: abs(i - mid)) - mid) < len(t) / 4 \
        else round(mid)
    return t[:cut].rstrip("，、；：,; ") + r"\N" + t[cut:].lstrip()


def ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def read_wav(path):
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getsampwidth() == 2, path
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        return x.reshape(-1, w.getnchannels())


def load_dub(path, tmp):
    out = tmp / (Path(path).stem + ".48k.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le",
                    str(out)], check=True)
    return read_wav(out)[:, 0]


def trim_silence(x, keep=0.03):
    """IndexTTS clips start with 0-710 ms of silence (median 250 ms, 09-30): trim it, or the dub lands late.
    Voice = 10 ms frames within 30 dB of the loudest frame; keep 30 ms before the first and after the last."""
    hop = SR // 100
    if len(x) < hop * 3:
        return x
    db = 20 * np.log10(np.sqrt((x[: len(x) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1)) + 1e-9)
    on = np.flatnonzero(db > db.max() - 30)
    a0 = max(0, on[0] * hop - int(keep * SR)); a1 = min(len(x), (on[-1] + 1) * hop + int(keep * SR))
    return x[a0:a1]


def rms(x):
    return float(np.sqrt(np.mean(np.square(x)) + 1e-12))


def main():
    ap = argparse.ArgumentParser()
    for k in ("--mt", "--tts", "--video", "--wav48", "--out"):
        ap.add_argument(k, required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--tag", default="AI 翻译配音")
    # the ffmpeg here wants NVENC API 13.1 = driver >= 610; works since the 09-30 upgrade to 616.92 (libx264 as fallback)
    ap.add_argument("--vcodec", default="h264_nvenc")
    a = ap.parse_args()
    out = Path(a.out); tmp = out / "tmp"; tmp.mkdir(parents=True, exist_ok=True)
    mt = json.loads(Path(a.mt).read_text(encoding="utf-8"))
    sents, zh_rows = mt["sentences"], mt["runs"]["q4"]["rows"]
    tts = {r["i"]: r for r in json.loads(Path(a.tts).read_text(encoding="utf-8"))["rows"]}
    tts_dir = Path(a.tts).parent
    dur = sents[-1]["e"] + 2.0

    # subtitles: Chinese above, English below, both on the sentence's own [s, e]
    ev = [f"Dialogue: 0,{ts(0)},{ts(dur)},TAG,,0,0,0,,{a.tag}"] if a.tag else []
    for i, s in enumerate(sents):
        zh = zh_rows[i]["zh"].strip().rstrip("。")
        # one event, Chinese on top and English below it, so libass stacks them and they can never overlap
        en = r"{\rEN}" + s["en"]
        ev.append(f"Dialogue: 1,{ts(s['s'])},{ts(s['e'])},ZH,,0,0,0,,{wrap_zh(zh) + r'\N' if zh else ''}{en}")
    ass = out / "subs.ass"
    ass.write_text(ASS_HEAD + "\n".join(ev) + "\n", encoding="utf-8-sig")

    # audio: original ducked under every dub, dubs placed at the sentence onset
    orig = read_wav(a.wav48)[: int(dur * SR)]
    n = len(orig)
    dub_track = np.zeros(n, np.float32)
    regions, placed = [], []
    for i, s in enumerate(sents):
        r = tts.get(i)
        if not r or "nat_s" not in r:
            continue
        f = tts_dir / f"{i:02d}_{'dur' if r.get('use') == 'dur' and 'dur_s' in r else 'nat'}.wav"
        x = trim_silence(load_dub(f, tmp))
        a0, b0 = int(s["s"] * SR), int(s["e"] * SR)
        x *= rms(orig[a0:b0].mean(axis=1)) / max(rms(x), 1e-4)  # match the original sentence's loudness
        limit = int((sents[i + 1]["s"] - 0.05) * SR) if i + 1 < len(sents) else n  # never run into the next onset
        room = max(0, min(len(x), limit - a0, n - a0))
        cut = room < len(x)
        if cut:
            x = x[:room].copy(); f50 = min(room, int(0.05 * SR)); x[room - f50:] *= np.linspace(1, 0, f50)
        dub_track[a0:a0 + len(x)] += x
        regions.append([s["s"], max(s["e"], s["s"] + len(x) / SR)])
        placed.append({"i": i, "file": f.name, "start": s["s"], "len": round(len(x) / SR, 2), "cut": cut})
    merged = []
    for s0, e0 in sorted(regions):
        if merged and s0 - merged[-1][1] < BRIDGE:
            merged[-1][1] = max(merged[-1][1], e0)
        else:
            merged.append([s0, e0])
    env_db = np.zeros(n, np.float32)
    for s0, e0 in merged:
        a1, a2 = int((s0 - ATTACK) * SR), int(s0 * SR)
        r1, r2 = int((e0 + 0.15) * SR), int((e0 + 0.15 + RELEASE) * SR)
        env_db[max(a2, 0):min(r1, n)] = DUCK_DB
        for lo, hi, down in ((a1, a2, True), (r1, r2, False)):
            lo_c, hi_c = max(lo, 0), min(hi, n)
            if hi_c > lo_c:
                ph = (np.arange(lo_c, hi_c) - lo) / (hi - lo)
                w = 0.5 - 0.5 * np.cos(np.pi * ph)  # 0 -> 1 raised cosine
                ramp = DUCK_DB * (w if down else 1 - w)
                env_db[lo_c:hi_c] = np.minimum(env_db[lo_c:hi_c], ramp)
    mix = orig * (10 ** (env_db / 20))[:, None] + dub_track[:, None]
    peak = float(np.abs(mix).max())
    if peak > 0.89:  # about -1 dBFS sample peak
        mix *= 0.89 / peak
    mix_wav = out / "mix.wav"
    with wave.open(str(mix_wav), "wb") as w:
        w.setnchannels(mix.shape[1]); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(mix, -1, 1) * 32767).astype(np.int16).tobytes())

    mp4 = out / "devday_B_demo.mp4"
    sub = str(ass).replace("\\", "/").replace(":", "\\:")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(a.start), "-t", f"{dur:.3f}", "-i", a.video, "-i", str(mix_wav),
                    "-map", "0:v", "-map", "1:a", "-vf", f"subtitles='{sub}':fontsdir='C\\:/Windows/Fonts'",
                    *(["-c:v", "h264_nvenc", "-preset", "p5", "-b:v", "8M"] if a.vcodec == "h264_nvenc"
                      else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]), "-c:a", "aac", "-b:a", "192k", str(mp4)], check=True)
    (out / "placed.json").write_text(json.dumps({"duration": dur, "duck_regions": merged, "dubs": placed},
                                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ok {mp4} {dur:.1f}s, {len(placed)} dubs, {len(merged)} duck regions, peak scale {min(1, 0.89 / peak):.2f}")


if __name__ == "__main__":
    main()
