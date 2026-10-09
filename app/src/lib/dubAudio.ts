// The player's sound: the video's own audio and the dub clips, mixed the way the OBS playout mixes them
// (livedub/playout.py): each dub normalised to DUB_RMS times the processor's gain, the original ducked by DUCK_DB with
// a raised-cosine ATTACK that ends at the line's start, HOLD then RELEASE after it, kept down across gaps < BRIDGE;
// a dub never runs into the next line's onset (50 ms fade where it is cut). Clips are scheduled on the AudioContext
// clock against the video's program time, LOOKAHEAD seconds ahead.
import { clipUrl } from "../api/client";
import type { Row } from "./rows";

const DUCK_DB = -15, ATTACK = 0.25, RELEASE = 1.0, BRIDGE = 1.5, HOLD = 0.15, DUB_RMS = 0.08;
const LOOKAHEAD = 1.0;

interface Clip { buf: AudioBuffer; start: number; dur: number; norm: number }  // trimmed span (s) and its scale

/** trim leading / trailing silence (frames 30 dB under the loudest, 30 ms kept) and the RMS scale to DUB_RMS */
function analyse(buf: AudioBuffer): Omit<Clip, "buf"> {
  const x = buf.getChannelData(0), sr = buf.sampleRate, hop = Math.max(1, Math.floor(sr / 100));
  const n = Math.floor(x.length / hop), db = new Float32Array(n);
  let max = -200;
  for (let i = 0; i < n; i++) {
    let e = 0;
    for (let j = i * hop; j < (i + 1) * hop; j++) e += x[j] * x[j];
    db[i] = 10 * Math.log10(e / hop + 1e-18);
    if (db[i] > max) max = db[i];
  }
  let a = 0, b = n - 1;
  while (a < n && db[a] <= max - 30) a++;
  while (b > a && db[b] <= max - 30) b--;
  const s0 = Math.max(0, a * hop - 0.03 * sr), s1 = Math.min(x.length, (b + 1) * hop + 0.03 * sr);
  let e = 0;
  for (let j = Math.floor(s0); j < s1; j++) e += x[j] * x[j];
  const rms = Math.sqrt(e / Math.max(1, s1 - s0));
  return { start: s0 / sr, dur: (s1 - s0) / sr, norm: DUB_RMS / Math.max(rms, 1e-4) };
}

const ease = (u: number) => 0.5 - 0.5 * Math.cos(Math.PI * Math.min(1, Math.max(0, u)));

export class DubMixer {
  readonly ctx = new AudioContext({ latencyHint: "playback" });
  private readonly duck = this.ctx.createGain();     // original: ducking
  private readonly orig = this.ctx.createGain();     // original: the user's volume
  private readonly dubs = this.ctx.createGain();     // all dubs: the user's volume
  private media: MediaElementAudioSourceNode | null = null;
  private readonly sources = new WeakMap<HTMLVideoElement, MediaElementAudioSourceNode>();
  private clips = new Map<string, Clip | "loading" | "failed">();
  private playing = new Map<string, AudioBufferSourceNode>();
  private started = new Set<string>();               // scheduled since the last seek / pause
  private session = "";

  constructor() {
    this.duck.connect(this.orig).connect(this.ctx.destination);
    this.dubs.connect(this.ctx.destination);
  }

  /** route a video element through the mixer (once per element) */
  attach(video: HTMLVideoElement) {
    if (this.media?.mediaElement === video) return;
    this.media?.disconnect();
    this.media = this.sources.get(video) ?? this.ctx.createMediaElementSource(video);
    this.sources.set(video, this.media);
    this.media.connect(this.duck);
  }

  /** the context starts suspended until a user gesture */
  resume() { if (this.ctx.state !== "running") this.ctx.resume().catch(() => {}); }

  volumes(orig: number, dub: number) {
    this.orig.gain.setTargetAtTime(orig, this.ctx.currentTime, 0.02);
    this.dubs.gain.setTargetAtTime(dub, this.ctx.currentTime, 0.02);
  }

  /** a new session: forget its clips */
  use(session: string) {
    if (session === this.session) return;
    this.stopAll();
    this.clips.clear();
    this.session = session;
  }

  /** load the clips of the lines from `from` to `to` (program seconds) that are not loaded yet */
  prefetch(rows: Row[], from: number, to: number) {
    for (const r of rows) {
      if (r.pt0 > to) break;
      if (r.pt0 < from || !r.clip || this.clips.has(r.clip)) continue;
      const file = r.clip, session = this.session;
      this.clips.set(file, "loading");
      fetch(clipUrl(session, file))
        .then((res) => (res.ok ? res.arrayBuffer() : Promise.reject(new Error(String(res.status)))))
        .then((b) => this.ctx.decodeAudioData(b))
        .then((buf) => { if (this.session === session) this.clips.set(file, { buf, ...analyse(buf) }); })
        .catch(() => { if (this.session === session) this.clips.set(file, "failed"); });
    }
    // decoded audio far behind the playhead is dropped (hours of dubs would not fit)
    for (const r of rows) {
      if (r.pt0 >= from - 120) break;
      if (r.clip && typeof this.clips.get(r.clip) === "object") this.clips.delete(r.clip);
    }
  }

  private clip(r: Row): Clip | undefined {
    const c = r.clip ? this.clips.get(r.clip) : undefined;
    return typeof c === "object" ? c : undefined;
  }

  /** stop every dub and lift the ducking (pause, seek, rate change) */
  stopAll() {
    for (const n of this.playing.values()) { try { n.stop(); } catch { /* not started */ } }
    this.playing.clear();
    this.started.clear();
    this.duck.gain.cancelScheduledValues(this.ctx.currentTime);
    this.duck.gain.setTargetAtTime(1, this.ctx.currentTime, 0.05);
  }

  /** called many times a second while playing: schedule the next dubs and set the ducking for program time pt */
  tick(rows: Row[], pt: number, on: boolean) {
    const now = this.ctx.currentTime;
    let lo = 0, hi = rows.length;                  // first line that could still be sounding (dubs are < 30 s)
    while (lo < hi) { const m = (lo + hi) >> 1; if (rows[m].pt0 < pt - 30) lo = m + 1; else hi = m; }
    const regions: [number, number][] = [];
    for (let i = lo; i < rows.length && rows[i].pt0 <= pt + LOOKAHEAD + ATTACK + 2; i++) {
      const r = rows[i], c = this.clip(r);
      if (!c) continue;
      const next = rows[i + 1]?.pt0 ?? Infinity;
      const end = Math.min(r.pt0 + c.dur, next - 0.05);
      regions.push([r.pt0, Math.max(r.pt1, end)]);
      if (!on || this.started.has(r.key) || r.pt0 > pt + LOOKAHEAD || pt >= end - 0.05) continue;
      const into = Math.max(0, pt - r.pt0);        // a seek into the middle of a line starts its dub there
      const when = now + Math.max(0, r.pt0 - pt);
      const node = this.ctx.createBufferSource(), g = this.ctx.createGain();
      node.buffer = c.buf;
      g.gain.value = c.norm * (r.gain ?? 1);
      const stopAt = when + (end - r.pt0 - into);
      if (end < r.pt0 + c.dur) {                    // cut short by the next line: fade the last 50 ms
        g.gain.setValueAtTime(g.gain.value, Math.max(when, stopAt - 0.05));
        g.gain.linearRampToValueAtTime(0, stopAt);
      }
      node.connect(g).connect(this.dubs);
      node.start(when, c.start + into, end - r.pt0 - into);
      node.onended = () => { if (this.playing.get(r.key) === node) this.playing.delete(r.key); };
      this.playing.set(r.key, node);
      this.started.add(r.key);
    }
    // ducking: the same curve as the playout, evaluated a little ahead so the 30 ms smoothing lands on time
    const t = pt + 0.03;
    let db = 0;
    if (on) {
      const merged: [number, number][] = [];
      for (const [s, e] of regions.sort((a, b) => a[0] - b[0])) {
        const m = merged[merged.length - 1];
        if (m && s - m[1] < BRIDGE) m[1] = Math.max(m[1], e); else merged.push([s, e]);
      }
      for (const [s, e] of merged) {
        const w = Math.min(ease((t - (s - ATTACK)) / ATTACK), ease(1 - (t - (e + HOLD)) / RELEASE));
        db = Math.min(db, DUCK_DB * w);
      }
    }
    this.duck.gain.setTargetAtTime(10 ** (db / 20), now, 0.03);
  }

  /** how many clips are decoded, and how many dubs were started since the last seek / pause */
  get ready() { let n = 0; for (const c of this.clips.values()) if (typeof c === "object") n++; return n; }
  get scheduled() { return this.started.size; }
  get duckGain() { return this.duck.gain.value; }
}

let mixer: DubMixer | null = null;
/** one mixer (one AudioContext) per window */
export function getMixer() {
  if (!mixer) {
    mixer = new DubMixer();
    if (import.meta.env.DEV) (window as unknown as { __mixer: DubMixer }).__mixer = mixer;   // for the dev tests
  }
  return mixer;
}
