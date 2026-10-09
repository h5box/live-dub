<script setup lang="ts">
// The subtitle list. With a playhead (a running session) rows are aired / on air / queued, the playhead is drawn,
// and "follow" keeps it about a third from the top; scrolling by hand stops following until "回到正在播出".
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import { clock } from "../lib/format";
import type { Row } from "../lib/rows";
import Icon from "./Icon.vue";
import SubtitleLine, { type Phase } from "./SubtitleLine.vue";

const props = withDefaults(defineProps<{
  rows: Row[];
  playhead?: number | null;    // PT on air now; null = no live playhead
  p0?: number;                 // PT of the program's first frame (time labels count from it)
  airBase?: number;            // air wall time = airBase + pt
  dubOn?: boolean;
  window?: number;             // render only the newest N rows (live); 0 = all
  startAt?: "top" | "bottom";  // where a fresh list opens when there is no playhead
  q?: string;                  // search text to highlight
  seekable?: boolean;          // clicking a line asks to play from it (emits seek)
}>(), { playhead: null, dubOn: true, window: 0, startAt: "top" });

const emit = defineEmits<{ seek: [pt: number] }>();
const box = ref<HTMLElement | null>(null);
const follow = ref(true);
const open = reactive(new Set<string>());
const limit = ref(props.window);
watch(() => props.window, (w) => (limit.value = w));

const live = computed(() => props.playhead !== null && props.playhead !== undefined);
const shown = computed(() => (limit.value && props.rows.length > limit.value ? props.rows.slice(-limit.value) : props.rows));
const hidden = computed(() => props.rows.length - shown.value.length);
const base = computed(() => props.p0 ?? props.rows[0]?.pt0 ?? 0);

/** index in `shown` of the first row not yet fully aired (== length when everything has aired) */
const cut = computed(() => {
  const r = shown.value, ph = props.playhead;
  if (!live.value || ph === null || ph === undefined) return r.length;
  let i = r.length;
  while (i > 0 && r[i - 1].pt1 > ph) i--;
  return i;
});
const onAirIdx = computed(() => {
  const i = cut.value, r = shown.value[i];
  return r && props.playhead !== null && props.playhead !== undefined && r.pt0 <= props.playhead ? i : -1;
});

function phase(i: number): Phase {
  if (!live.value) return "plain";
  return i < cut.value ? "aired" : i === onAirIdx.value ? "onair" : "queued";
}
function eta(i: number, r: Row) {
  return phase(i) === "queued" ? Math.max(0, Math.ceil(r.pt0 - (props.playhead as number))) : undefined;
}
function progress(i: number, r: Row) {
  if (i !== onAirIdx.value) return undefined;
  return Math.min(1, Math.max(0, ((props.playhead as number) - r.pt0) / Math.max(0.1, r.pt1 - r.pt0)));
}
/** the playhead line, when no row is on air: above the next queued row, or under the last aired one */
function edge(i: number): "before" | "after" | undefined {
  if (!live.value || onAirIdx.value >= 0) return undefined;
  if (i === cut.value) return "before";
  if (cut.value === shown.value.length && i === shown.value.length - 1) return "after";
  return undefined;
}
const edgeLabel = computed(() => {
  const nxt = shown.value[cut.value];
  return nxt ? `播放位置 · 下一句 ${Math.max(0, Math.ceil(nxt.pt0 - (props.playhead as number)))} 秒后` : "播放位置 · 等下一句";
});

function toggle(k: string) { if (open.has(k)) open.delete(k); else open.add(k); }

// ---- scrolling ------------------------------------------------------------------------------------------
const focusKey = computed(() => (live.value ? shown.value[Math.min(cut.value, shown.value.length - 1)]?.key : undefined));

function scrollToFocus(smooth = true) {
  const el = box.value, k = focusKey.value;
  if (!el || !k) return;
  const row = el.querySelector<HTMLElement>(`[data-k="${k}"]`);
  if (!row) return;
  const top = Math.max(0, row.offsetTop - el.clientHeight * 0.34);
  if (Math.abs(el.scrollTop - top) > 2) el.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
}
watch([focusKey, () => shown.value.length], () => {
  if (live.value && follow.value) nextTick(() => scrollToFocus());
});
watch(live, (on) => { if (on) { follow.value = true; nextTick(() => scrollToFocus(false)); } });
// a new session (the list emptied and refilled) starts at the right place again
watch(() => props.rows[0]?.key, () => {
  open.clear();
  follow.value = true;
  nextTick(() => {
    if (!box.value) return;
    if (live.value) scrollToFocus(false);
    else box.value.scrollTop = props.startAt === "bottom" ? box.value.scrollHeight : 0;
  });
});
onMounted(() => nextTick(() => {
  if (!box.value) return;
  if (live.value) scrollToFocus(false);
  else if (props.startAt === "bottom") box.value.scrollTop = box.value.scrollHeight;
}));

// only the user's own scrolling stops following (programmatic scrolls do not raise these)
function userScroll() { if (live.value) follow.value = false; }
function onKey(e: KeyboardEvent) {
  if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End", " "].includes(e.key)) userScroll();
}
function backToLive() { follow.value = true; scrollToFocus(); }

/** scroll a row into view (from the chart); stops following */
function reveal(key: string) {
  const i = props.rows.findIndex((r) => r.key === key);
  if (i < 0) return;
  if (limit.value && i < props.rows.length - limit.value) limit.value = props.rows.length - i + 20;
  if (live.value) follow.value = false;
  nextTick(() => {
    const el = box.value?.querySelector<HTMLElement>(`[data-k="${key}"]`);
    if (!el || !box.value) return;
    box.value.scrollTo({ top: Math.max(0, el.offsetTop - box.value.clientHeight * 0.34), behavior: "smooth" });
    el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
  });
}
defineExpose({ reveal });
</script>

<template>
  <div class="tl">
    <div ref="box" class="scroll" @wheel.passive="userScroll" @touchmove.passive="userScroll" @keydown="onKey"
         @mousedown="($event.target === box) && userScroll()">
      <button v-if="hidden > 0" class="more btn ghost sm" @click="limit += 300">
        显示更早的 {{ Math.min(hidden, 300) }} 句（共 {{ rows.length }} 句）</button>
      <slot v-if="!rows.length" name="empty" />
      <div v-else-if="live" class="headpad" />
      <SubtitleLine v-for="(r, i) in shown" :key="r.key" :data-k="r.key" :row="r" :phase="phase(i)"
                    :time="clock(r.pt0 - base)" :show-spk="i === 0 || shown[i - 1].spk !== r.spk" :dub-on="dubOn"
                    :open="open.has(r.key)" :eta="eta(i, r)" :progress="progress(i, r)"
                    :air-at="airBase !== undefined ? airBase + r.pt0 : undefined"
                    :edge="edge(i)" :edge-label="edge(i) ? edgeLabel : undefined" :q="q" :seekable="seekable"
                    @toggle="toggle(r.key)" @seek="emit('seek', r.pt0)" />
      <div v-if="live && rows.length" class="tail" />
    </div>
    <transition name="fade">
      <button v-if="live && !follow" class="back btn primary sm" @click="backToLive">
        <Icon name="jump" :size="14" />回到正在播的位置</button>
    </transition>
  </div>
</template>

<style scoped>
.tl { position: relative; flex: 1; min-height: 0; display: flex; }
.scroll { position: relative; flex: 1; overflow-y: auto; overscroll-behavior: contain; }  /* rows' offsetParent */
.more { display: flex; margin: 8px auto; }
.tail { height: 40vh; }
.headpad { height: 12px; }   /* room for the playhead label above the first line */       /* room to keep the last lines near the playhead position */
.back { position: absolute; left: 50%; bottom: 16px; transform: translateX(-50%); height: 32px; padding: 0 14px;
  box-shadow: var(--shadow); border-radius: 16px; }
.fade-enter-active, .fade-leave-active { transition: opacity .15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
:deep(.line.flash) { animation: flash 1.4s ease-out; }
@keyframes flash { 0%, 30% { background: var(--accent-soft); } 100% { background: transparent; } }
</style>
