<script setup lang="ts">
import { computed } from "vue";
import { hms, sec1 } from "../lib/format";
import { DUB_HINT, DUB_LABEL, dubView, type Row } from "../lib/rows";
import { LOW_MARGIN_S } from "../lib/stats";
import Icon from "./Icon.vue";

/** aired / onair / queued while a session runs; plain when there is no playhead (history, ended) */
export type Phase = "aired" | "onair" | "queued" | "plain";

const props = defineProps<{
  row: Row; phase: Phase; time: string; showSpk: boolean; dubOn: boolean; open: boolean;
  eta?: number;          // queued: whole seconds until it airs
  progress?: number;     // onair: 0..1
  airAt?: number;        // wall time it airs (for the details)
  edge?: "before" | "after";   // draw the playhead line on this edge
  edgeLabel?: string;
  q?: string;                  // search text to highlight
  seekable?: boolean;          // a click plays from this line (the details open with the ⓘ button)
}>();
const emit = defineEmits<{ toggle: []; seek: [] }>();
const activate = () => (props.seekable ? emit("seek") : emit("toggle"));

const dv = computed(() => dubView(props.row, props.phase !== "queued" && props.phase !== "onair"));
const low = computed(() => props.row.slack !== undefined && props.row.slack < LOW_MARGIN_S);
const dur = computed(() => props.row.pt1 - props.row.pt0);

/** text split around case-insensitive matches of q */
function parts(text: string): { t: string; m: boolean }[] {
  const q = props.q?.trim().toLowerCase();
  if (!q) return [{ t: text, m: false }];
  const out: { t: string; m: boolean }[] = [], low = text.toLowerCase();
  let i = 0;
  for (let j = low.indexOf(q); j >= 0; j = low.indexOf(q, i)) {
    if (j > i) out.push({ t: text.slice(i, j), m: false });
    out.push({ t: text.slice(j, j + q.length), m: true });
    i = j + q.length;
  }
  if (i < text.length) out.push({ t: text.slice(i), m: false });
  return out;
}
</script>

<template>
  <div class="line" :class="[phase, { open, low, seekable, ['edge-' + edge]: !!edge }]" role="button" tabindex="0"
       :aria-expanded="open" :title="seekable ? '从这句开始播放' : undefined" @click="activate" @keydown.enter.prevent="activate">
    <span v-if="edge" class="edge-label">{{ edgeLabel }}</span>
    <div class="t">
      <span class="num">{{ time }}</span>
      <span v-if="showSpk" class="spk">说话人 {{ row.spk + 1 }}</span>
    </div>
    <div class="txt">
      <p class="zh"><template v-if="!row.zh">（没有译文）</template><template v-for="(x, i) in parts(row.zh)" :key="i"><mark v-if="x.m">{{ x.t }}</mark><template v-else>{{ x.t }}</template></template></p>
      <p class="en"><template v-for="(x, i) in parts(row.en)" :key="i"><mark v-if="x.m">{{ x.t }}</mark><template v-else>{{ x.t }}</template></template></p>
    </div>
    <div class="st">
      <span v-if="phase === 'onair'" class="now"><i />正在播</span>
      <span v-else-if="phase === 'queued' && eta !== undefined" class="eta num">{{ eta }} 秒后</span>
      <template v-if="dubOn">
        <span v-if="dv === 'ok'" class="dub" :title="DUB_HINT.ok"><Icon name="check" :size="13" class="okic" />{{ DUB_LABEL.ok }}</span>
        <span v-else-if="dv === 'pending'" class="dub" :title="DUB_HINT.pending"><Icon name="spinner" :size="12" class="spin" />{{ DUB_LABEL.pending }}</span>
        <span v-else-if="dv === 'short' || dv === 'none'" class="dub" :title="DUB_HINT[dv]">{{ DUB_LABEL[dv] }}</span>
        <span v-else class="chip" :class="dv === 'failed' ? 'critical' : 'warning'" :title="row.err || DUB_HINT[dv]">
          <Icon :name="dv === 'failed' ? 'error' : 'alert'" :size="12" />{{ DUB_LABEL[dv] }}</span>
      </template>
      <span v-if="low" class="chip warning" title="字幕写出时离默认播放时刻不到 5 秒">
        <Icon name="alert" :size="12" />余量 {{ sec1(row.slack) }} 秒</span>
      <button v-if="seekable" class="info btn ghost sm icon" :class="{ on: open }" title="耗时明细" @click.stop="emit('toggle')">
        <Icon name="info" :size="13" /></button>
    </div>
    <div v-if="phase === 'onair'" class="prog"><i :style="{ transform: `scaleX(${progress ?? 0})` }" /></div>
    <div v-if="open" class="detail num" @click.stop>
      <span>说话人 {{ row.spk + 1 }}</span>
      <span>时长 {{ sec1(dur) }} 秒</span>
      <span v-if="row.mt !== undefined">翻译用时 {{ row.mt.toFixed(2) }} 秒</span>
      <span v-if="row.slack !== undefined">字幕余量 {{ sec1(row.slack) }} 秒</span>
      <span v-if="row.tts !== undefined">配音合成 {{ row.tts.toFixed(2) }} 秒</span>
      <span v-if="row.factor !== undefined">时长缩放 ×{{ row.factor.toFixed(2) }}{{ row.clamped ? "（到上限）" : "" }}</span>
      <span v-if="row.left !== undefined">配音余量 {{ sec1(row.left) }} 秒</span>
      <span v-if="airAt">播出 {{ hms(airAt) }}</span>
      <span class="muted">PT {{ row.pt0.toFixed(2) }}–{{ row.pt1.toFixed(2) }}</span>
      <span v-if="row.err" class="err">{{ row.err }}</span>
    </div>
  </div>
</template>

<style scoped>
.line { position: relative; display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; column-gap: 14px;
  padding: 10px 16px 10px 14px; border-left: 3px solid transparent; cursor: pointer; outline-offset: -2px; }
.line:hover { background: var(--surface-2); }
.line + .line { box-shadow: inset 0 1px 0 var(--border); }
.t { display: flex; flex-direction: column; gap: 3px; padding-top: 2px; color: var(--ink-3); font-size: 12px; }
.spk { align-self: flex-start; font-size: 11px; line-height: 18px; padding: 0 6px; border-radius: 4px;
  background: var(--surface-2); color: var(--ink-2); white-space: nowrap; }
.zh { font-size: 15px; line-height: 1.55; color: var(--ink-1); word-break: break-word; }
.en { margin-top: 2px; font-size: 12.5px; line-height: 1.45; color: var(--ink-3); word-break: break-word; }
.st { display: flex; flex-direction: column; align-items: flex-end; gap: 5px; padding-top: 3px; min-width: 96px;
  font-size: 12px; color: var(--ink-3); }
.dub { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.okic { color: var(--good); }
.eta { color: var(--ink-2); white-space: nowrap; }
.now { display: inline-flex; align-items: center; gap: 6px; color: var(--accent); font-weight: 600; white-space: nowrap; }
.now i { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); animation: pulse 1.2s ease-in-out infinite; }

/* phases while on air */
.aired .txt, .aired .t { opacity: .6; }
.onair { background: var(--accent-soft); border-left-color: var(--accent); }
.onair:hover { background: var(--accent-soft); }
.onair .zh { font-weight: 500; }
.prog { position: absolute; left: 0; right: 0; bottom: 0; height: 2px; }
.prog i { display: block; height: 100%; background: var(--accent); transform-origin: left; transition: transform .25s linear; }

/* the playhead between two lines */
.edge-before::before, .edge-after::after { content: ""; position: absolute; left: -3px; right: 0; height: 2px;
  background: var(--accent); z-index: 1; }
.edge-before::before { top: -1px; }
.edge-after::after { bottom: -1px; }
.edge-label { position: absolute; right: 12px; z-index: 2; padding: 0 8px; height: 18px; line-height: 18px; border-radius: 9px;
  background: var(--accent); color: var(--on-accent); font-size: 11px; font-weight: 600; white-space: nowrap; }
.edge-before .edge-label { top: -10px; }
.edge-after .edge-label { bottom: -10px; }

mark { background: var(--warning-soft); color: inherit; border-radius: 2px; box-shadow: 0 0 0 1px var(--warning-soft); }
.info { opacity: 0; margin-top: -2px; }
.line:hover .info, .info.on, .line:focus-visible .info { opacity: 1; }
.detail { grid-column: 2 / -1; display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 8px; padding: 8px 10px;
  border-radius: var(--radius-sm); background: var(--surface-2); color: var(--ink-2); font-size: 12px; cursor: text; }
.detail .err { color: var(--ink-1); flex-basis: 100%; }
</style>
