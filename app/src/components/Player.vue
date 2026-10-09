<script setup lang="ts">
// The in-app player: the run's chunks as HLS (hls.js), the subtitles drawn over the picture, the dubs mixed in by
// WebAudio (lib/dubAudio.ts). Program time PT comes from each chunk's EXT-X-PROGRAM-DATE-TIME. While the session
// runs, "follow" keeps the picture at the default schedule (first frame + delay D, the run's anchor); pausing,
// skipping or clicking a line stops following until 回到实时.
import Hls, { type Fragment, type LevelDetails } from "hls.js";
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { BASE } from "../api/client";
import type { Anchor } from "../api/types";
import { getMixer } from "../lib/dubAudio";
import { clock } from "../lib/format";
import { load, save } from "../lib/prefs";
import type { Row } from "../lib/rows";
import { live, player } from "../stores/live";
import Icon from "./Icon.vue";

const props = defineProps<{
  media: string | null;      // playlist path (/api/sessions/<name>/media/index.m3u8)
  session: string | null;    // run name (for the dub clips)
  rows: Row[];
  anchor: Anchor | null;
  running: boolean;          // the session is running: follow the default schedule
  dubOn: boolean;            // the session makes dubs
}>();

const box = ref<HTMLElement | null>(null);
const video = ref<HTMLVideoElement | null>(null);
const mixer = getMixer();
let hls: Hls | null = null;
let frags: Fragment[] = [];
let loaded = "";            // the playlist hls.js was given
let retry = 0;

const st = reactive({
  pt: null as number | null, first: null as number | null, edge: null as number | null, vod: false,
  playing: false, waiting: false, ended: false, needClick: false, error: "", fs: false, vol: false,
  started: false,           // has played (or been sought) since loading: before that the paused first frame is a preview
});
const prefs = reactive(load("player", { orig: 1, dub: 1, subs: "zhen" as "zhen" | "zh" | "en" | "off", box: false }));
watch(prefs, () => save("player", { ...prefs }), { deep: true });
watch(() => [prefs.orig, prefs.dub], () => mixer.volumes(prefs.orig, prefs.dub), { immediate: true });
watch(() => prefs.dub === 0 || !props.dubOn, (off) => { if (off) mixer.stopAll(); });

// ---- program time <-> media time (each fragment knows its start in both) -----------------------------------
const pdt = (f: Fragment) => (f.programDateTime ?? 0) / 1000;
function ptAt(t: number): number | null {
  if (!frags.length) return null;
  let lo = 0, hi = frags.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (frags[m].start <= t) lo = m; else hi = m - 1; }
  const f = frags[lo];
  return f.programDateTime === null ? null : pdt(f) + (t - f.start);
}
function timeAt(pt: number): number | null {
  if (!frags.length) return null;
  let lo = 0, hi = frags.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (pdt(frags[m]) <= pt) lo = m; else hi = m - 1; }
  const f = frags[lo];
  return f.start + Math.min(Math.max(0, pt - pdt(f)), f.duration);
}

// ---- loading ---------------------------------------------------------------------------------------------
function teardown() {
  clearTimeout(retry);
  hls?.destroy(); hls = null; frags = []; loaded = "";
  Object.assign(st, { pt: null, first: null, edge: null, vod: false, ended: false, needClick: false, error: "", started: false });
  mixer.stopAll();
  player.pt = null;
}

function setup() {
  teardown();
  const v = video.value;
  if (!v || !props.media) return;
  mixer.use(props.session ?? "");
  mixer.attach(v);
  if (!Hls.isSupported()) { st.error = "这个窗口不能播放 HLS（缺 MediaSource）"; return; }
  hls = new Hls({ enableWorker: true, backBufferLength: 900, maxBufferLength: 40, liveDurationInfinity: true,
    liveSyncDurationCount: 3, manifestLoadingMaxRetry: 30, levelLoadingMaxRetry: 30, fragLoadingMaxRetry: 10 });
  const onLevel = (details: LevelDetails) => {
    frags = details.fragments;
    const a = frags[0], z = frags[frags.length - 1];
    st.first = a ? pdt(a) : null;
    st.edge = z ? pdt(z) + z.duration : null;
    st.vod = !details.live;
  };
  hls.on(Hls.Events.LEVEL_LOADED, (_, d) => onLevel(d.details));
  hls.on(Hls.Events.LEVEL_UPDATED, (_, d) => onLevel(d.details));
  hls.on(Hls.Events.ERROR, (_, d) => {
    if (!d.fatal) return;
    const manifest = [Hls.ErrorDetails.MANIFEST_LOAD_ERROR, Hls.ErrorDetails.MANIFEST_LOAD_TIMEOUT,
      Hls.ErrorDetails.MANIFEST_PARSING_ERROR, Hls.ErrorDetails.LEVEL_EMPTY_ERROR].includes(d.details);
    if (manifest) retry = window.setTimeout(setup, 2000);          // not there yet (the run is starting): again
    else if (d.type === Hls.ErrorTypes.NETWORK_ERROR) retry = window.setTimeout(() => hls?.startLoad(), 1000);
    else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls?.recoverMediaError();
    else st.error = `播放出错：${d.details}`;
  });
  loaded = props.media;
  hls.loadSource(BASE + props.media);
  hls.attachMedia(v);
  if (import.meta.env.DEV) Object.assign(window, { __hls: hls, __ptAt: ptAt, __target: target });   // for the dev tests
}
/** a running session has a picture once its anchor exists (written when the first chunk closed) */
const canLoad = computed(() => !!props.media && (!props.running || !!props.anchor));
watch([() => props.media, canLoad], () => {
  if (!canLoad.value) { if (loaded) teardown(); }
  else if (loaded !== props.media) setup();
});
onMounted(() => { if (canLoad.value) setup(); });

// ---- controls ----------------------------------------------------------------------------------------------
function play() {
  mixer.resume();
  st.needClick = false;
  video.value?.play().catch(() => (st.needClick = true));
}
function toggle() {
  const v = video.value;
  if (!v || !frags.length) return;
  if (v.paused) { if (st.ended) seekPT(st.first ?? 0); play(); }
  else { v.pause(); player.follow = false; }
}
/** show PT `pt`; any seek by the user stops following the default schedule */
function seekPT(pt: number, follow = false) {
  const v = video.value;
  if (!v || st.first === null || st.edge === null) return;
  const t = timeAt(Math.min(Math.max(pt, st.first), st.edge - 0.3));
  if (t === null) return;
  player.follow = follow;
  st.ended = false;
  st.started = true;
  mixer.stopAll();
  v.currentTime = t;
}
const skip = (d: number) => { if (st.pt !== null) seekPT(st.pt + d); };
function backToLive() { player.follow = true; play(); }
function fullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else box.value?.requestFullscreen().catch(() => {});
}
const onFs = () => (st.fs = !!document.fullscreenElement);

function onKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement;
  if (!props.media || ["INPUT", "SELECT", "TEXTAREA"].includes(t?.tagName) || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === " ") { e.preventDefault(); toggle(); }
  else if (e.key === "ArrowLeft") { e.preventDefault(); skip(-5); }
  else if (e.key === "ArrowRight") { e.preventDefault(); skip(5); }
  else if (e.key === "f" || e.key === "F") fullscreen();
}

// ---- the loop: position, following, dubs ----------------------------------------------------------------
/** where the default schedule is now (anchor: PT p is shown at T0 + D + (p - p0)) */
const target = () => (props.anchor ? props.anchor.p0 + (Date.now() / 1000 + live.skew - props.anchor.T0 - props.anchor.delay) : null);
let raf = 0, lastPub = 0, lastFetch = 0, lastKick = 0;
function loop() {
  raf = requestAnimationFrame(loop);
  const v = video.value;
  if (!v || !hls) return;
  const pt = frags.length ? ptAt(v.currentTime) : null;
  st.pt = pt;
  const now = performance.now();
  if (now - lastPub > 200) { player.pt = st.started ? pt : null; lastPub = now; }
  // follow the default schedule while the session runs
  const tg = props.running && player.follow ? target() : null;
  if (tg !== null && st.first !== null && st.edge !== null && tg >= st.first && !v.seeking) {
    if (v.paused && !st.needClick) {
      if (now - lastKick > 1500) { lastKick = now; seekPT(tg, true); play(); }   // one kick, then let it load
    }
    else if (pt !== null && Math.abs(pt - tg) > 2.5 && tg < st.edge - 0.5) seekPT(tg, true);
  }
  const playing = !v.paused && !v.seeking && !st.waiting && pt !== null;
  if (pt !== null) {
    if (now - lastFetch > 1000) { mixer.prefetch(props.rows, pt - 5, pt + 60); lastFetch = now; }
    if (playing) mixer.tick(props.rows, pt, props.dubOn && prefs.dub > 0);
  }
}
onMounted(() => {
  raf = requestAnimationFrame(loop);
  window.addEventListener("keydown", onKey);
  document.addEventListener("fullscreenchange", onFs);
  player.seek = (pt: number) => { seekPT(pt); if (video.value?.paused) play(); };
});
onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  window.removeEventListener("keydown", onKey);
  document.removeEventListener("fullscreenchange", onFs);
  player.seek = null;
  teardown();
});

function onPause() { st.playing = false; mixer.stopAll(); }
function onSeeking() { mixer.stopAll(); }
function onPlaying() { st.playing = true; st.waiting = false; st.started = true; }
function onEnded() { st.ended = true; st.playing = false; mixer.stopAll(); }

// ---- what is on screen -----------------------------------------------------------------------------------
const line = computed(() => {
  const pt = st.pt, r = props.rows;
  if (pt === null || !r.length) return null;
  let lo = 0, hi = r.length - 1;
  while (lo < hi) { const m = (lo + hi + 1) >> 1; if (r[m].pt0 <= pt) lo = m; else hi = m - 1; }
  return r[lo].pt0 <= pt && pt < r[lo].pt1 ? r[lo] : null;
});
/** the Chinese line in rows of about ZH_ROW full-width characters (Latin letters count about half), broken at
 *  punctuation where it can be and never inside a Latin word: the cheapest split, where a row over ZH_ROW costs
 *  a lot, a break away from punctuation costs some, every row and unevenness cost a little */
const ZH_ROW = 28;
const width = (x: string) => [...x].reduce((w, c) => w + (c.charCodeAt(0) < 0x2e80 ? 0.55 : 1), 0);
function wrapZh(t: string): string[] {
  if (width(t) <= ZH_ROW) return [t];
  const n = t.length, rows = Math.ceil(width(t) / ZH_ROW), even = width(t) / rows;
  const punct = (i: number) => "，、；：。！？,;".includes(t[i - 1]);
  const inWord = (i: number) => /[A-Za-z0-9]/.test(t[i - 1]) && /[A-Za-z0-9]/.test(t[i]);
  const cost = new Array<number>(n + 1).fill(Infinity), prev = new Array<number>(n + 1).fill(0);
  cost[0] = 0;
  for (let i = 1; i <= n; i++) {
    if (i < n && inWord(i)) continue;
    for (let j = Math.max(0, i - 60); j < i; j++) {
      if (cost[j] === Infinity) continue;
      const w = width(t.slice(j, i));
      const c = cost[j] + 3 + 0.15 * Math.abs(w - even) + 100 * Math.max(0, w - ZH_ROW) + (i < n && !punct(i) ? 10 : 0);
      if (c < cost[i]) { cost[i] = c; prev[i] = j; }
    }
  }
  const out: string[] = [];
  for (let i = n; i > 0; i = prev[i]) out.unshift(t.slice(prev[i], i).trim().replace(/[，、；：,;]+$/, ""));
  return out.filter(Boolean);
}
const zhRows = computed(() => wrapZh(line.value?.zh ?? ""));
const p0 = computed(() => props.anchor?.p0 ?? st.first ?? 0);
const countdown = computed(() => {
  void live.now;                          // re-evaluate with the store's clock
  const tg = props.running ? target() : null;
  return tg !== null && st.first !== null && tg < st.first ? Math.ceil(st.first - tg) : null;
});
const veil = computed(() => {
  if (st.error) return { kind: "error", text: st.error };
  if (!props.media) return { kind: "idle", text: "" };
  if (props.running && !props.anchor) return { kind: "wait", text: "正在拉流…" };
  if (countdown.value !== null) return { kind: "count", text: `${countdown.value}` };
  if (st.first === null) return { kind: "wait", text: props.running ? "等第一段画面…" : "读取中…" };
  if (st.needClick) return { kind: "click", text: "点击开始播放" };
  if (st.waiting && !video.value?.paused) return { kind: "wait", text: "缓冲中…" };
  if (st.ended) return { kind: "ended", text: "播完了" };
  return null;
});
/** seconds behind the default schedule (0 when following) */
const behind = computed(() => {
  void live.now;
  const tg = props.running ? target() : null;
  return tg !== null && st.pt !== null ? tg - st.pt : null;
});

// ---- the scrubber (program time between the first and the newest chunk) ---------------------------------
const span = computed(() => (st.first !== null && st.edge !== null ? Math.max(1, st.edge - st.first) : 1));
const pos = (pt: number | null) => (pt === null || st.first === null ? 0 : Math.min(100, Math.max(0, (100 * (pt - st.first)) / span.value)));
const hoverPT = ref<number | null>(null);
function ptFromEvent(e: PointerEvent) {
  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
  return (st.first ?? 0) + ((e.clientX - r.left) / r.width) * span.value;
}
function scrub(e: PointerEvent) { seekPT(ptFromEvent(e)); if (video.value?.paused) play(); }
</script>

<template>
  <div ref="box" class="player" :class="{ fs: st.fs }">
    <div class="screen" @click="props.media && toggle()" @dblclick.prevent="fullscreen">
      <video ref="video" playsinline @pause="onPause" @play="mixer.resume()" @playing="onPlaying"
             @waiting="st.waiting = true" @seeking="onSeeking" @ended="onEnded" />
      <div v-if="line && prefs.subs !== 'off'" class="subs" :class="{ box: prefs.box }">
        <p v-if="prefs.subs !== 'en' && line.zh" class="zh"><template v-for="(r, i) in zhRows" :key="i"><br v-if="i" />{{ r }}</template></p>
        <p v-if="prefs.subs !== 'zh' && line.en" class="en">{{ line.en }}</p>
      </div>
      <div v-if="veil" class="veil" :class="veil.kind">
        <template v-if="veil.kind === 'idle'"><slot name="idle" /></template>
        <template v-else-if="veil.kind === 'count'">
          <b class="num">{{ veil.text }}</b><span>秒后开始播放（等翻译和配音先跑在前面）</span>
        </template>
        <button v-else-if="veil.kind === 'click'" class="big" @click.stop="play"><Icon name="play" :size="28" /></button>
        <template v-else>
          <Icon v-if="veil.kind === 'wait'" name="spinner" :size="22" class="spin" />
          <Icon v-else-if="veil.kind === 'error'" name="error" :size="22" />
          <span>{{ veil.text }}</span>
        </template>
      </div>
    </div>

    <div class="bar" :class="{ off: !props.media || st.first === null }">
      <button class="btn ghost icon" :title="st.playing ? '暂停（空格）' : '播放（空格）'" @click="toggle">
        <Icon :name="st.playing ? 'pause' : 'play'" :size="16" /></button>
      <button class="btn ghost icon" title="后退 10 秒（←）" @click="skip(-10)"><Icon name="back10" :size="17" /></button>
      <button class="btn ghost icon" title="前进 10 秒（→）" @click="skip(10)"><Icon name="fwd10" :size="17" /></button>
      <span class="time num">{{ st.pt !== null ? clock(st.pt - p0) : "—" }}</span>
      <div class="scrub" @pointerdown="scrub" @pointermove="hoverPT = ptFromEvent($event)" @pointerleave="hoverPT = null">
        <div class="track"><i class="done" :style="{ width: pos(st.pt) + '%' }" /></div>
        <i v-if="props.running && target() !== null" class="mark" :style="{ left: pos(target()) + '%' }" title="默认延时的位置" />
        <i class="thumb" :style="{ left: pos(st.pt) + '%' }" />
        <span v-if="hoverPT !== null" class="tip num" :style="{ left: pos(hoverPT) + '%' }">{{ clock(hoverPT - p0) }}</span>
      </div>
      <button v-if="props.running" class="btn sm live" :class="{ on: player.follow && (behind ?? 0) < 3 }"
              :title="player.follow ? '跟着默认延时播放' : '回到默认延时的位置'" @click="backToLive">
        <i />{{ player.follow && (behind ?? 0) < 3 ? "实时" : behind !== null && behind > 0 ? `晚 ${Math.round(behind)} 秒` : "回到实时" }}
      </button>
      <select v-model="prefs.subs" class="select subsel" title="字幕">
        <option value="zhen">中英</option><option value="zh">中文</option><option value="en">原文</option><option value="off">关字幕</option>
      </select>
      <div class="volwrap">
        <button class="btn ghost icon" title="音量" @click="st.vol = !st.vol"><Icon name="voice" :size="17" /></button>
        <div v-if="st.vol" class="vol card" @click.stop>
          <label><span>原声</span><input v-model.number="prefs.orig" type="range" min="0" max="1" step="0.05" /></label>
          <label><span>配音</span><input v-model.number="prefs.dub" type="range" min="0" max="1.5" step="0.05" :disabled="!props.dubOn" /></label>
          <label class="chk"><input v-model="prefs.box" type="checkbox" />字幕加底</label>
        </div>
      </div>
      <button class="btn ghost icon" title="全屏（F）" @click="fullscreen"><Icon :name="st.fs ? 'shrink' : 'expand'" :size="16" /></button>
    </div>
  </div>
</template>

<style scoped>
.player { display: flex; flex-direction: column; min-width: 0; background: #000; border-radius: var(--radius); overflow: hidden;
  border: 1px solid var(--border); }
.player.fs { border-radius: 0; border: 0; }
.screen { position: relative; aspect-ratio: 16 / 9; container-type: size; background: #000; cursor: pointer; }
.player.fs .screen { flex: 1; aspect-ratio: auto; }
video { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }

.subs { position: absolute; left: 4%; right: 4%; bottom: 5.5cqh; display: flex; flex-direction: column; align-items: center;
  gap: 0.6cqh; text-align: center; pointer-events: none; color: #fff; }
.subs p { margin: 0; line-height: 1.3; text-wrap: balance;
  text-shadow: 0 0 0.35cqh #000, 0 0 0.35cqh #000, 0 0 0.6cqh #000, 0 0.25cqh 0.8cqh rgba(0, 0, 0, 0.7); }
.subs .zh { font-size: 5.3cqh; font-weight: 700; font-family: "Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif; }
.subs .en { font-size: 3.3cqh; font-weight: 500; }
.subs.box p { background: rgba(0, 0, 0, 0.55); padding: 0.3cqh 1.2cqh; border-radius: 0.6cqh; }

.veil { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; color: #fff; background: rgba(0, 0, 0, 0.45); font-size: 14px; text-align: center; padding: 16px; }
.veil.idle { background: #0d0d0d; cursor: default; }
.veil.count b { font-size: 56px; font-weight: 600; line-height: 1; }
.veil.count span { color: rgba(255, 255, 255, 0.75); }
.veil.error .ic { color: var(--critical); }
.veil .big { width: 72px; height: 72px; border-radius: 50%; border: 0; background: rgba(255, 255, 255, 0.92); color: #111;
  display: grid; place-items: center; cursor: pointer; padding-left: 5px; }

.bar { display: flex; align-items: center; gap: 4px; padding: 6px 8px; background: var(--surface); border-top: 1px solid var(--border); }
.bar.off { opacity: .55; pointer-events: none; }
.player.fs .bar { position: absolute; left: 0; right: 0; bottom: 0; opacity: 0; transition: opacity .2s; }
.player.fs:hover .bar { opacity: 1; }
.time { min-width: 48px; text-align: center; font-size: 12px; color: var(--ink-2); }
.scrub { position: relative; flex: 1; min-width: 60px; height: 24px; cursor: pointer; touch-action: none; }
.track { position: absolute; left: 0; right: 0; top: 10px; height: 4px; border-radius: 2px; background: var(--surface-3); overflow: hidden; }
.track .done { display: block; height: 100%; background: var(--accent); }
.mark { position: absolute; top: 6px; width: 2px; height: 12px; margin-left: -1px; background: var(--onair); border-radius: 1px; }
.thumb { position: absolute; top: 6px; width: 12px; height: 12px; margin-left: -6px; border-radius: 50%; background: var(--accent);
  box-shadow: 0 0 0 2px var(--surface); }
.tip { position: absolute; bottom: 26px; transform: translateX(-50%); padding: 2px 6px; border-radius: 4px; font-size: 11px;
  background: var(--ink-1); color: var(--plane); pointer-events: none; white-space: nowrap; }
.live { gap: 6px; height: 26px; font-size: 12px; }
.live i { width: 7px; height: 7px; border-radius: 50%; background: var(--ink-3); }
.live.on i { background: var(--onair); animation: pulse 1.6s ease-in-out infinite; }
.subsel { width: auto; height: 26px; padding: 0 24px 0 8px; font-size: 12px; background-position: calc(100% - 13px) 11px, calc(100% - 8px) 11px; }
.volwrap { position: relative; }
.vol { position: absolute; right: 0; bottom: 38px; z-index: 5; display: flex; flex-direction: column; gap: 8px; padding: 10px 12px;
  width: 200px; box-shadow: var(--shadow); }
.vol label { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--ink-2); }
.vol label span { width: 30px; }
.vol input[type=range] { flex: 1; accent-color: var(--accent); }
.vol .chk { gap: 6px; }
</style>
