"""Speaker IDs for the live processor v0: TitaNet-L embedding per translation unit + online cosine clustering.

Checks the idea offline on segment B: units from the v2 translation run, reference labels from the Nemotron
low-latency diarization (majority overlap). Reports clusters found and units whose cluster disagrees with the
reference (after mapping each cluster to its majority reference label). Runs on CPU in the asr venv.

usage: python spk_cluster.py MT_JSON DIAR_JSON WAV16K [--thr 0.5,0.6,0.7]
"""
import argparse
import json
from collections import Counter

import numpy as np
import soundfile as sf
import torch
from pathlib import Path

MODEL = Path.home() / "models/titanet/speakerverification_en_titanet_large.nemo"


def speaker_of(s, e, segs):
    ov = {}
    for s2, e2, k in segs:
        o = min(e, e2) - max(s, s2)
        if o > 0:
            ov[k] = ov.get(k, 0) + o
    return max(ov, key=ov.get) if ov else None


class OnlineClusters:
    def __init__(self, thr):
        self.thr, self.cent, self.n = thr, [], []

    def assign(self, emb):
        if self.cent:
            sims = [float(emb @ c) for c in self.cent]
            k = int(np.argmax(sims))
            if sims[k] >= self.thr:
                c = self.cent[k] * self.n[k] + emb
                self.cent[k] = c / np.linalg.norm(c); self.n[k] += 1
                return k, sims[k]
        self.cent.append(emb); self.n.append(1)
        return len(self.cent) - 1, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mt"); ap.add_argument("diar"); ap.add_argument("wav"); ap.add_argument("--thr", default="0.5,0.6,0.7")
    a = ap.parse_args()
    from nemo.collections.asr.models import EncDecSpeakerLabelModel
    model = EncDecSpeakerLabelModel.restore_from(str(MODEL), map_location="cpu").eval()
    units = json.loads(Path(a.mt).read_text(encoding="utf-8"))["sentences"]
    segs = json.loads(Path(a.diar).read_text(encoding="utf-8"))["runs"]["low_1.04s"]["segments"]
    audio, sr = sf.read(a.wav, dtype="float32")
    embs, ref = [], []
    import time
    t = time.time()
    for u in units:
        x = audio[int(u["s"] * sr): int(u["e"] * sr)]
        with torch.no_grad():
            emb, _ = model.infer_segment(x)
        v = emb.squeeze().cpu().numpy(); embs.append(v / np.linalg.norm(v))
        ref.append(speaker_of(u["s"], u["e"], segs))
    print(f"{len(units)} embeddings in {time.time() - t:.1f}s on CPU; reference speakers {Counter(ref)}")
    for thr in (float(x) for x in a.thr.split(",")):
        oc = OnlineClusters(thr)
        lab = [oc.assign(e)[0] for e in embs]
        major = {c: Counter(r for l, r in zip(lab, ref) if l == c).most_common(1)[0][0] for c in set(lab)}
        wrong = [i for i, (l, r) in enumerate(zip(lab, ref)) if major[l] != r]
        split = {r: len({l for l, rr in zip(lab, ref) if rr == r}) for r in set(ref)}
        print(f"thr {thr}: clusters {len(oc.cent)}  wrong {len(wrong)}/{len(units)} {wrong}  clusters per ref speaker {split}")


if __name__ == "__main__":
    main()
