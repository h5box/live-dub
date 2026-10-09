"""Single-pass duration-controlled IndexTTS-2.5 for live-dub.

Adapted from index-tts (https://github.com/index-tts/index-tts, indextts/infer_v2_5.py), which is under the bilibili
Model Use License; so is this file (not the project's license). Any modifications made to the original model in this
Derivative Work are not endorsed, warranted, or guaranteed by the original right-holder of the original model, and the
original right-holder disclaims all liability related to this Derivative Work. See THIRD_PARTY.md.

Upstream (indextts/infer_v2_5.py) controls duration only through `duration_factor`, which scales the S2M target mel
length (frames = semantic feature frames x 1.72 x factor; 2 feature frames per GPT code). The factor has to be known before the call, so the M0 pipeline ran
the whole sentence twice (natural pass -> measure -> duration pass). Here the GPT runs once, the natural length is
read off its semantic codes, the factor is computed from the target, and S2M + BigVGAN run once.

The upstream repo (the index-tts checkout, $LIVEDUB_INDEXTTS, default ~/live-dub/index-tts) is not modified: DubEngine subclasses IndexTTS2 and re-implements the body
of infer_generator in stages. Other differences from upstream, all opt-in or harmless:
  * speaker conditioning is cached per reference path (dict), not in a single slot, so alternating speakers do not
    recompute w2v-bert + CAM++ + prompt regulator every sentence;
  * diffusion_steps / num_beams / max_mel_tokens are constructor knobs (upstream hard-codes 25 / 3 / 1500);
  * the output is trimmed of leading / trailing silence (keeps `keep_ms` of lead-in).

Must be imported with cwd = that checkout (upstream sets HF_HUB_CACHE relative to cwd on import).
"""
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import torchaudio

INDEXTTS = Path(os.environ.get("LIVEDUB_INDEXTTS") or Path.home() / "live-dub/index-tts")
sys.path.insert(0, str(INDEXTTS))
from indextts.infer_v2_5 import IndexTTS2, apply_pronunciation_annotations  # noqa: E402
from indextts.utils.common import fade_out_tail  # noqa: E402
from indextts.utils.tokenizer import lang_to_token  # noqa: E402

from dubsvc.audio import trim_silence  # noqa: E402,F401

SR = 22050
HOP = 256
CODE_TO_FRAMES = 1.72
SILENT_CODE = 52
FACTOR_MIN, FACTOR_MAX = 0.85, 1.25


def _sync():
    if torch.cuda.is_available():
        torch.cuda.synchronize()


class DubEngine(IndexTTS2):
    def __init__(self, model_dir, diffusion_steps=25, num_beams=3, max_mel_tokens=800, cfg_rate=0.7, edge_pad_s=0.0, **kw):
        kw.setdefault("use_bf16", True)
        kw.setdefault("use_cuda_kernel", False)
        kw.setdefault("use_deepspeed", False)
        super().__init__(cfg_path=str(Path(model_dir) / "config.yaml"), model_dir=str(model_dir), **kw)
        self.low_vram = False  # never let upstream split behind our back (16G card anyway)
        self.diffusion_steps = diffusion_steps
        self.num_beams = num_beams
        self.max_mel_tokens = max_mel_tokens
        self.cfg_rate = cfg_rate
        self.edge_pad_s = edge_pad_s
        self._spk_cache = {}
        eng = getattr(self.gpt, "accel_engine", None)
        if eng is not None:
            # upstream passes only temperature to the accel engine (its defaults: top_k 50, top_p 1.0, no repetition
            # penalty, no beams); give it the same top_k / top_p as the HF path so the two are closer.
            gen = eng.generate
            eng.generate = lambda *a, **k: gen(*a, **{**k, "top_k": 30, "top_p": 0.8})
        self._emo_cache = {}

    # ---------- conditioning ----------
    @torch.no_grad()
    def spk_cond(self, path):
        c = self._spk_cache.get(path)
        if c is not None:
            return c
        audio, sr = self._load_and_cut_audio(path, 15, False)
        audio_22k = torchaudio.transforms.Resample(sr, 22050)(audio)
        audio_16k = torchaudio.transforms.Resample(sr, 16000)(audio)
        inputs = self.extract_features(audio_16k, sampling_rate=16000, return_tensors="pt")
        spk_cond_emb = self.get_emb(inputs["input_features"].to(self.device), inputs["attention_mask"].to(self.device))
        ref_mel = self.mel_fn(audio_22k.to(spk_cond_emb.device).float())
        ref_target_lengths = torch.LongTensor([ref_mel.size(2)]).to(ref_mel.device)
        feat = torchaudio.compliance.kaldi.fbank(audio_16k.to(ref_mel.device), num_mel_bins=80, dither=0,
                                                 sample_frequency=16000)
        feat = feat - feat.mean(dim=0, keepdim=True)
        style = self.campplus_model(feat.unsqueeze(0))
        prompt_condition = self.s2mel.models['length_regulator'](spk_cond_emb, ylens=ref_target_lengths,
                                                                 n_quantizers=3, f0=None)[0]
        c = (spk_cond_emb, style, prompt_condition, ref_mel)
        if len(self._spk_cache) >= 16:
            self._spk_cache.pop(next(iter(self._spk_cache)))
        self._spk_cache[path] = c
        return c

    @torch.no_grad()
    def emo_cond(self, path):
        c = self._emo_cache.get(path)
        if c is not None:
            return c
        emo_audio, _ = self._load_and_cut_audio(path, 15, False, sr=16000)
        x = self.extract_features(emo_audio, sampling_rate=16000, return_tensors="pt")
        c = self.get_emb(x["input_features"].to(self.device), x["attention_mask"].to(self.device))
        if len(self._emo_cache) >= 4:
            self._emo_cache.pop(next(iter(self._emo_cache)))
        self._emo_cache[path] = c
        return c

    def text_tokens(self, text, lang):
        lang_prefix = f'<|{lang.lower()}|> '
        text = self.text_process.clean_pattern.sub(lambda x: self.text_process.char_rep_map[x.group()], text)
        if lang.lower() in ['zh', 'zhen', 'en']:
            text = self.text_process.normalize(text)
        if lang.lower() in ['ja', 'zh', 'zhen', 'en']:
            text = text.lower()
        text = apply_pronunciation_annotations(text)
        if lang.lower() == 'ja':
            text = self.ja_text_process.process_ja_text(text)
        text = re.sub(r'<\|([^|]+)\|>', lambda m: f'<|{m.group(1).upper()}|>', text)
        segs = self.split_text_by_tokens(text, 120, lang_prefix)
        out = []
        for s in segs:
            toks = self.tokenizer.encode(lang_prefix + s, allowed_special='all')
            toks = torch.IntTensor(toks).unsqueeze(0).to(self.device)
            out.append(F.pad(toks, (0, 1), value=1))
        return out, torch.LongTensor([lang_to_token(lang)]).to(self.device)

    # ---------- stages ----------
    @torch.no_grad()
    def gpt_codes(self, spk_cond_emb, emo_cond_emb, style, text_tokens, lang_t, spk_path, emo_alpha=1.0):
        with torch.amp.autocast(text_tokens.device.type, enabled=self.dtype is not None, dtype=self.dtype):
            L = torch.tensor([spk_cond_emb.shape[-1]], device=text_tokens.device)
            Le = torch.tensor([emo_cond_emb.shape[-1]], device=text_tokens.device)
            emovec = self.gpt.merge_emovec(spk_cond_emb, emo_cond_emb, L, Le, alpha=emo_alpha)
            codes, _ = self.gpt.inference_speech(
                spk_cond_emb, text_tokens, lang_t, emo_cond_emb, cond_lengths=L, emo_cond_lengths=Le,
                emo_vec=emovec, campplus_embedding=style, wav=spk_path, do_sample=True, top_p=0.8, top_k=30,
                temperature=0.8, num_return_sequences=1, length_penalty=0.0, num_beams=self.num_beams,
                repetition_penalty=10.0, max_generate_length=self.max_mel_tokens)
        code = codes[0]
        hit = (code == self.stop_mel_token).nonzero(as_tuple=False)
        n = hit[0, 0].item() if hit.numel() else len(code)
        codes = codes[:, :n]
        # semantic codes -> semantic features; S2M works on these (2 feature frames per code for IndexTTS-2.5)
        S_infer = self.semantic_codec.decode(codes)
        return codes, S_infer, bool(hit.numel())

    @torch.no_grad()
    def s2m_vocode(self, S_infer, factor, style, prompt_condition, ref_mel):
        target_lengths = torch.LongTensor([int(S_infer.shape[1] * CODE_TO_FRAMES * factor)]).to(S_infer.device)
        cond = self.s2mel.models['length_regulator'](S_infer, ylens=target_lengths, n_quantizers=3, f0=None)[0]
        cat_condition = torch.cat([prompt_condition, cond], dim=1)
        vc = self.s2mel.models['cfm'].inference(cat_condition, torch.LongTensor([cat_condition.size(1)]).to(cond.device),
                                                ref_mel, style, None, self.diffusion_steps,
                                                inference_cfg_rate=self.cfg_rate)
        vc = vc[:, :, ref_mel.size(-1):]
        _sync()
        t = time.perf_counter()
        wav = self.bigvgan(vc.float()).squeeze().unsqueeze(0)
        _sync()
        return torch.clamp(32767 * wav, -32767.0, 32767.0).cpu(), time.perf_counter() - t

    @staticmethod
    def edge_silence(codes):
        c = codes[0].tolist()
        lead = next((i for i, x in enumerate(c) if x != SILENT_CODE), len(c))
        trail = next((i for i, x in enumerate(reversed(c)) if x != SILENT_CODE), len(c))
        return lead, trail

    # ---------- public ----------
    def synth(self, text, spk_ref, emo_ref=None, target_s=None, lang="zh", factor=None, trim=True,
              trim_aware=True, emo_alpha=1.0, keep_ms=30):
        """One pass. Returns dict(wav=float32 np @ SR, natural_s, factor, clamped, ...timings).
        factor given -> used as-is (no target logic). target_s None -> natural speed.
        trim_aware: natural length for the factor excludes leading/trailing silent codes (target is speech span)."""
        T = {}
        _sync(); t0 = time.perf_counter()
        spk_cond_emb, style, prompt_condition, ref_mel = self.spk_cond(spk_ref)
        if emo_ref is None:
            emo_ref, emo_alpha = spk_ref, 1.0
        emo_cond_emb = self.emo_cond(emo_ref)
        toks, lang_t = self.text_tokens(text, lang)
        _sync(); T["prep_s"] = time.perf_counter() - t0

        t = time.perf_counter()
        seg_codes, seg_S, stopped = [], [], True
        for tt in toks:
            c, S, ok = self.gpt_codes(spk_cond_emb, emo_cond_emb, style, tt, lang_t, spk_ref, emo_alpha)
            seg_codes.append(c); seg_S.append(S); stopped &= ok
        _sync(); T["gpt_s"] = time.perf_counter() - t

        n_codes = sum(c.shape[-1] for c in seg_codes)
        gap_s = 0.2 * (len(seg_codes) - 1)
        natural_s = sum(int(S.shape[1] * CODE_TO_FRAMES) for S in seg_S) * HOP / SR + gap_s
        lead, _ = self.edge_silence(seg_codes[0])
        _, trail = self.edge_silence(seg_codes[-1])
        per_code_s = (natural_s - gap_s) / max(n_codes, 1)
        edge_s = (lead + trail) * per_code_s
        # trim_aware: the target is the speech span (onset..offset), but the vocoded clip carries ~edge_pad_s of
        # near-silence at its ends that trimming removes; subtract it from the natural length before the ratio.
        basis = natural_s - edge_s - (self.edge_pad_s if trim_aware else 0.0) if trim_aware else natural_s
        clamped = False
        if factor is None:
            if target_s:
                raw = (target_s - gap_s) / max(basis - gap_s, 1e-3)
                factor = min(max(raw, FACTOR_MIN), FACTOR_MAX)
                clamped = factor != raw
            else:
                factor = 1.0

        t = time.perf_counter(); voc = 0.0
        wavs = []
        with torch.no_grad():
            for S in seg_S:
                w, v = self.s2m_vocode(S, factor, style, prompt_condition, ref_mel)
                wavs.append(w); voc += v
        T["s2m_s"] = time.perf_counter() - t - voc
        T["vocoder_s"] = voc
        wavs = self.insert_interval_silence(wavs, sampling_rate=SR, interval_silence=200)
        wav = fade_out_tail(torch.cat(wavs, dim=1), SR)
        x = (wav[0].numpy() / 32768.0).astype(np.float32)
        raw_s = len(x) / SR
        lead_cut = 0.0
        if trim:
            x, lead_cut = trim_silence(x, SR, keep_ms=keep_ms)
        _sync(); T["wall_s"] = time.perf_counter() - t0
        return dict(wav=x, sr=SR, natural_s=round(natural_s, 3), natural_speech_s=round(natural_s - edge_s, 3),
                    factor=round(float(factor), 4), clamped=clamped, raw_s=round(raw_s, 3),
                    audio_s=round(len(x) / SR, 3), lead_cut_s=round(lead_cut, 3), n_codes=n_codes,
                    stop_hit=stopped, edge_codes=(lead, trail), **{k: round(v, 3) for k, v in T.items()})
