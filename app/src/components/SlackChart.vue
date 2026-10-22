<script setup lang="ts">
// Margin before air over program time: how many seconds each subtitle (series 1) and each dub clip (series 2)
// was ready before its air time. One y axis (seconds), a dashed reference at the warning margin, legend plus
// labels at the line ends, crosshair + tooltip on hover; clicking a point asks the timeline to show that line.
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { clock, sec1 } from "../lib/format";
import type { Row } from "../lib/rows";
import { LOW_MARGIN_S } from "../lib/stats";

const props = withDefaults(defineProps<{
  rows: Row[];
  p0?: number;            // PT of the program start (x counts from it)
  span?: number;          // show only the last `span` seconds
  now?: number | null;    // program seconds on air now (live): the window ends no earlier than this
  height?: number;
  delay?: number;         // the session's D: the most margin a line can have
  dubOn?: boolean;
}>(), { span: 0, now: null, height: 150, delay: 30, dubOn: true });
const emit = defineEmits<{ pick: [key: string] }>();

const wrap = ref<HTMLElement | null>(null);
const width = ref(300);
let ro: ResizeObserver | null = null;
onMounted(() => {
  ro = new ResizeObserver(([e]) => (width.value = Math.max(160, Math.floor(e.contentRect.width))));
  if (wrap.value) ro.observe(wrap.value);
});
onBeforeUnmount(() => ro?.disconnect());

const M = { t: 8, r: 34, b: 20, l: 28 };
const iw = computed(() => width.value - M.l - M.r);
const ih = computed(() => props.height - M.t - M.b);
const base = computed(() => props.p0 ?? props.rows[0]?.pt0 ?? 0);

interface Pt { t: number; v: number; key: string }
const subPts = computed<Pt[]>(() => props.rows.filter((r) => r.slack !== undefined)
  .map((r) => ({ t: r.pt0 - base.value, v: r.slack as number, key: r.key })));
const dubPts = computed<Pt[]>(() => !props.dubOn ? [] : props.rows.filter((r) => r.left !== undefined)
  .map((r) => ({ t: r.pt0 - base.value, v: r.left as number, key: r.key })));

const xdom = computed<[number, number]>(() => {
  const last = Math.max(subPts.value.at(-1)?.t ?? 0, dubPts.value.at(-1)?.t ?? 0, props.now ?? 0);
  let x0 = 0, x1 = Math.max(last, 60);
  if (props.span) { x0 = Math.max(0, x1 - props.span); x1 = Math.max(x1, x0 + Math.min(props.span, 120)); }
  return [x0, x1];
});
const inWin = (p: Pt) => p.t >= xdom.value[0] - 1 && p.t <= xdom.value[1] + 1;
const subVis = computed(() => subPts.value.filter(inWin));
const dubVis = computed(() => dubPts.value.filter(inWin));

const ydom = computed<[number, number]>(() => {
  const vs = [...subVis.value, ...dubVis.value].map((p) => p.v);
  const lo = Math.min(0, ...vs), hi = Math.max(props.delay, ...vs);
  return [Math.floor(lo / 5) * 5, Math.ceil(hi / 10) * 10];
});

const x = (t: number) => M.l + ((t - xdom.value[0]) / Math.max(1e-6, xdom.value[1] - xdom.value[0])) * iw.value;
const y = (v: number) => M.t + ih.value - ((v - ydom.value[0]) / (ydom.value[1] - ydom.value[0])) * ih.value;

/** a line through the points, broken where the program had no lines for over a minute */
function path(pts: Pt[]) {
  let d = "", prev: Pt | null = null;
  for (const p of pts) {
    d += `${!prev || p.t - prev.t > 60 ? "M" : "L"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`;
    prev = p;
  }
  return d;
}
const subPath = computed(() => path(subVis.value));
const dubPath = computed(() => path(dubVis.value));

const yTicks = computed(() => {
  const [a, b] = ydom.value, step = b - a > 40 ? 20 : 10, out = [];
  for (let v = Math.ceil(a / step) * step; v <= b; v += step) out.push(v);
  return out;
});
const xTicks = computed(() => {
  const [a, b] = xdom.value, want = Math.max(2, Math.floor(iw.value / 70));
  const step = [15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200].find((s) => (b - a) / s <= want) ?? 7200;
  const out = [];
  for (let t = Math.ceil(a / step) * step; t <= b; t += step) out.push(t);
  return out;
});

// labels at the line ends, pushed apart when they would overlap
const ends = computed(() => {
  const out: { cls: string; text: string; y: number }[] = [];
  const s = subVis.value.at(-1), d = dubVis.value.at(-1);
  if (s) out.push({ cls: "s1", text: "字幕", y: y(s.v) });
  if (d) out.push({ cls: "s2", text: "配音", y: y(d.v) });
  if (out.length === 2 && Math.abs(out[0].y - out[1].y) < 12) {
    const mid = (out[0].y + out[1].y) / 2, up = out[0].y <= out[1].y ? 0 : 1;
    out[up].y = mid - 6; out[1 - up].y = mid + 6;
  }
  return out;
});

// ---- hover ----------------------------------------------------------------------------------------------
const hover = ref<{ t: number; key: string; s?: number; d?: number; zh: string; px: number } | null>(null);
const byKey = computed(() => new Map(props.rows.map((r) => [r.key, r])));
function nearest(pts: Pt[], t: number): Pt | undefined {
  let best: Pt | undefined, bd = Infinity;
  for (const p of pts) { const d = Math.abs(p.t - t); if (d < bd) { bd = d; best = p; } }
  return best;
}
function move(e: PointerEvent) {
  const box = (e.currentTarget as SVGElement).getBoundingClientRect();
  const px = e.clientX - box.left;
  const t = xdom.value[0] + ((px - M.l) / iw.value) * (xdom.value[1] - xdom.value[0]);
  const p = nearest(subVis.value.length ? subVis.value : dubVis.value, t);
  if (!p || Math.abs(x(p.t) - px) > 40) { hover.value = null; return; }
  const r = byKey.value.get(p.key);
  hover.value = { t: p.t, key: p.key, s: r?.slack, d: props.dubOn ? r?.left : undefined, zh: r?.zh ?? "", px: x(p.t) };
}
const tipLeft = computed(() => (hover.value && hover.value.px > width.value - 170 ? hover.value.px - 172 : (hover.value?.px ?? 0) + 12));
</script>

<template>
  <div ref="wrap" class="chart">
    <div class="legend">
      <span><i class="sw s1" />字幕余量</span>
      <span v-if="dubOn"><i class="sw s2" />配音余量</span>
      <span class="unit">单位：秒（完成时距播出）</span>
    </div>
    <div class="plot">
      <svg :width="width" :height="height" role="img"
           :aria-label="`余量：字幕 ${subVis.length} 句${dubOn ? `，配音 ${dubVis.length} 句` : ''}`"
           @pointermove="move" @pointerleave="hover = null" @click="hover && emit('pick', hover.key)">
        <g class="grid">
          <line v-for="v in yTicks" :key="'y' + v" :x1="M.l" :x2="M.l + iw" :y1="y(v)" :y2="y(v)" :class="{ zero: v === 0 }" />
        </g>
        <g class="axis">
          <text v-for="v in yTicks" :key="'yl' + v" :x="M.l - 6" :y="y(v) + 3.5" text-anchor="end">{{ v }}</text>
          <text v-for="t in xTicks" :key="'xl' + t" :x="x(t)" :y="height - 5" text-anchor="middle">{{ clock(t) }}</text>
        </g>
        <g class="ref">
          <line :x1="M.l" :x2="M.l + iw" :y1="y(LOW_MARGIN_S)" :y2="y(LOW_MARGIN_S)" />
          <text :x="M.l + iw + 4" :y="y(LOW_MARGIN_S) + 3.5">{{ LOW_MARGIN_S }} 秒</text>
        </g>
        <path v-if="dubOn" class="line s2" :d="dubPath" />
        <path class="line s1" :d="subPath" />
        <text v-for="e in ends" :key="e.cls" class="end" :x="M.l + iw + 4" :y="e.y + 3.5">{{ e.text }}</text>
        <template v-if="hover">
          <line class="cross" :x1="hover.px" :x2="hover.px" :y1="M.t" :y2="M.t + ih" />
          <circle v-if="hover.d !== undefined && dubOn" class="dot s2" :cx="hover.px" :cy="y(hover.d)" r="4" />
          <circle v-if="hover.s !== undefined" class="dot s1" :cx="hover.px" :cy="y(hover.s)" r="4" />
        </template>
        <text v-if="!subVis.length && !dubVis.length" class="empty" :x="M.l + iw / 2" :y="M.t + ih / 2" text-anchor="middle">暂无数据</text>
      </svg>
      <div v-if="hover" class="tip" :style="{ left: tipLeft + 'px' }">
        <div class="tt num">{{ clock(hover.t) }}</div>
        <div v-if="hover.s !== undefined" class="tr"><i class="sw s1" />字幕 <b class="num">{{ sec1(hover.s) }}</b> 秒</div>
        <div v-if="hover.d !== undefined" class="tr"><i class="sw s2" />配音 <b class="num">{{ sec1(hover.d) }}</b> 秒</div>
        <div class="tz">{{ hover.zh }}</div>
        <div class="th">点击定位到对应字幕</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chart { position: relative; min-width: 0; }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 12px; font-size: 12px; color: var(--ink-2);
  margin-bottom: 6px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend .unit { color: var(--ink-3); margin-left: auto; font-size: 11.5px; }
.sw { display: inline-block; width: 14px; height: 2px; border-radius: 1px; }
.sw.s1 { background: var(--series-1); }
.sw.s2 { background: var(--series-2); }
.plot { position: relative; }
svg { display: block; cursor: crosshair; overflow: visible; }
.grid line { stroke: var(--grid); stroke-width: 1; shape-rendering: crispEdges; }
.grid line.zero { stroke: var(--axis); }
.axis text, .ref text, .end, .empty { fill: var(--ink-3); font-size: 10.5px; font-family: var(--font); }
.axis text { font-variant-numeric: tabular-nums; }
.end { fill: var(--ink-2); font-size: 11px; }
.ref line { stroke: var(--ink-3); stroke-width: 1; stroke-dasharray: 3 3; }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.line.s1 { stroke: var(--series-1); }
.line.s2 { stroke: var(--series-2); }
.cross { stroke: var(--ink-3); stroke-width: 1; }
.dot { stroke: var(--surface); stroke-width: 2; }
.dot.s1 { fill: var(--series-1); }
.dot.s2 { fill: var(--series-2); }
.tip { position: absolute; top: 0; width: 160px; padding: 8px 10px; border-radius: 8px; pointer-events: none; z-index: 5;
  background: var(--surface); border: 1px solid var(--border-2); box-shadow: var(--shadow); font-size: 12px; }
.tt { color: var(--ink-3); margin-bottom: 3px; }
.tr { display: flex; align-items: center; gap: 6px; color: var(--ink-2); }
.tr b { color: var(--ink-1); font-weight: 600; }
.tz { margin-top: 5px; color: var(--ink-1); line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2;
  -webkit-box-orient: vertical; overflow: hidden; }
.th { margin-top: 4px; color: var(--ink-3); font-size: 11px; }
</style>
