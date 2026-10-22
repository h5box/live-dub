<script setup lang="ts">
// Top of the watch view: what to translate (a link or a local video) and how, while nothing runs; the running
// session's title, state and stop button while one does.
import { computed, onUnmounted, ref } from "vue";
import { hm } from "../lib/format";
import { active, form, formPreset as preset, live, services, session, startSession, stopSession }
  from "../stores/live";
import Icon from "./Icon.vue";
import ObsCard from "./ObsCard.vue";

const inTauri = "__TAURI_INTERNALS__" in window;
const advanced = ref(false);
const VIDEO_EXT = ["mp4", "mkv", "mov", "webm", "ts", "m2ts", "flv", "avi", "m4v", "wmv"];

function pick(name: string) {
  const old = preset.value;
  form.preset = name;
  const p = preset.value;
  if (!p) return;
  if (!form.src.trim() || form.src === old?.src) form.src = p.src;   // a preset with its own source fills it in
  form.delay = p.delay_s;
}
async function chooseFile() {
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const f = await open({ multiple: false, directory: false, title: "选择视频",
      filters: [{ name: "视频", extensions: VIDEO_EXT }, { name: "所有文件", extensions: ["*"] }] });
    if (typeof f === "string") form.src = f;
  } catch (e) { live.error = `无法打开文件对话框：${(e as Error).message ?? e}`; }
}
const step = (d: number) => { form.delay = Math.min(120, Math.max(10, Math.round(form.delay + d))); };

const src = computed(() => form.src.trim());
const kind = computed(() => (!src.value ? null : /^https?:\/\//i.test(src.value) ? "link" : "file"));
const defaultModel = computed(() => preset.value?.mt_model || live.models.find((m) => m.default)?.key || "");
const model = computed(() => form.mt_model || defaultModel.value);
const modelInfo = computed(() => live.models.find((m) => m.key === model.value));
const delayOk = computed(() => Number.isFinite(form.delay) && form.delay >= 10 && form.delay <= 120);
const dubDown = computed(() => services.value?.dub?.state === "down");
const canStart = computed(() => live.link && !!src.value && !!preset.value && !live.busy && delayOk.value &&
  modelInfo.value?.ready !== false);
const start = () => startSession({ preset: form.preset, src: src.value, delay: form.delay, dub: form.dub, obs: form.obs,
  mt_model: model.value });

// stopping takes two clicks: the first arms the button for 4 s
const armed = ref(false);
let disarm = 0;
function stop() {
  if (!armed.value) {
    armed.value = true;
    disarm = window.setTimeout(() => (armed.value = false), 4000);
    return;
  }
  armed.value = false;
  clearTimeout(disarm);
  stopSession();
}
onUnmounted(() => clearTimeout(disarm));

const s = computed(() => session.value);
const lastStep = computed(() => s.value?.log.at(-1)?.[1] ?? "");
const STATE = { starting: "准备中", running: "翻译中", stopping: "停止中" } as Record<string, string>;
</script>

<template>
  <section class="bar card" :class="active && s ? s.state : 'idle'">
    <template v-if="active && s">
      <div class="tally" :class="s.state">
        <Icon v-if="s.state !== 'running'" name="spinner" :size="14" class="spin" />
        <i v-else class="dot" />
        <span>{{ STATE[s.state] }}</span>
      </div>
      <div class="what">
        <div class="title" :title="s.src ?? s.title">{{ s.title }}</div>
        <div class="meta">
          <span v-if="s.state === 'starting'" class="steptext"><Icon name="chevron" :size="12" />{{ lastStep }}</span>
          <template v-else>
            <span>{{ s.kind === "file" ? "本地视频" : "链接" }}</span><i>·</i>
            <span>延时 {{ s.delay_s }} 秒</span><i>·</i>
            <span>{{ s.dub ? "中文配音" : "仅字幕" }}</span>
            <template v-if="s.mt_model"><i>·</i><span>翻译 {{ s.mt_model }}</span></template>
            <template v-if="s.started"><i>·</i><span>{{ hm(s.started) }} 开始</span></template>
          </template>
        </div>
      </div>
      <ObsCard v-if="s.obs" compact />
      <button class="btn danger lg stop" :class="{ armed }" :disabled="!!live.busy || s.state === 'stopping'" @click="stop">
        <Icon name="stop" :size="14" />
        {{ live.busy === "stop" || s.state === "stopping" ? "停止中…" : armed ? "确认停止" : s.state === "starting" ? "取消" : "停止" }}
      </button>
    </template>

    <template v-else>
      <div class="row1">
        <label class="srcbox">
          <Icon :name="kind === 'file' ? 'film' : 'link'" :size="16" />
          <input v-model="form.src" class="input" spellcheck="false"
                 placeholder="粘贴直播或视频链接（YouTube、m3u8 等），或输入本地视频路径" @keydown.enter="canStart && start()" />
        </label>
        <button v-if="inTauri" class="btn" @click="chooseFile"><Icon name="folder" :size="15" />选择视频</button>
        <button class="btn primary lg go" :disabled="!canStart" @click="start">
          <Icon :name="live.busy === 'start' ? 'spinner' : 'play'" :size="14" :class="{ spin: live.busy === 'start' }" />
          {{ live.busy === "start" ? "正在开始…" : "开始" }}
        </button>
      </div>
      <div class="row2">
        <label class="switch"><input v-model="form.dub" type="checkbox" />中文配音</label>
        <span>延时 {{ form.delay }} 秒</span>
        <button class="btn sm" :aria-expanded="advanced" @click="advanced = !advanced">{{ advanced ? '收起高级选项' : '高级选项' }}</button>
      </div>
      <div v-if="advanced" class="row2">
        <label class="opt" title="场景预设包含场景说明、术语表和识别热词，影响翻译用词；内置预设在 configs/，自定义预设放在 configs/local/">场景
          <select class="select" :value="form.preset" @change="pick(($event.target as HTMLSelectElement).value)">
            <option v-for="p in live.presets" :key="p.name" :value="p.name">{{ p.title }}{{ p.local ? "（自定义）" : "" }}</option>
          </select>
        </label>
        <div class="opt">延时
          <div class="stepper">
            <button class="btn icon sm" :disabled="form.delay <= 10" title="减 5 秒" @click="step(-5)"><Icon name="minus" :size="13" /></button>
            <input v-model.number="form.delay" class="input num" type="number" min="10" max="120" aria-label="延时（秒）" />
            <button class="btn icon sm" :disabled="form.delay >= 120" title="加 5 秒" @click="step(5)"><Icon name="plus" :size="13" /></button>
          </div>秒
        </div>
        <label class="opt">翻译
          <select v-model="form.mt_model" class="select" :title="modelInfo ? `${modelInfo.label}：${modelInfo.detail}` : ''">
            <option value="">默认（{{ live.models.find((m) => m.key === defaultModel)?.label ?? defaultModel }}）</option>
            <option v-for="m in live.models" :key="m.key" :value="m.key" :disabled="!m.ready" :title="m.note || m.detail">
              {{ m.label }} · {{ m.online ? "线上" : "本地" }}{{ m.ready ? "" : "（不可用）" }}</option>
          </select>
        </label>
        <label class="switch" title="将带字幕和配音的画面推流到本机 MediaMTX，供 OBS 使用"><input v-model="form.obs" type="checkbox" />输出到 OBS</label>
      </div>
      <div class="hint">
        <span v-if="kind === 'link'"><Icon name="link" :size="12" />自动识别直播或点播；点播会提前处理字幕</span>
        <span v-else-if="kind === 'file'"><Icon name="film" :size="12" />本地视频：提前处理字幕，可暂停和跳转</span>
        <span v-else>画面延后播放，为识别、翻译和配音留出时间</span>
        <span v-if="!delayOk" class="chip warning"><Icon name="alert" :size="12" />延时要在 10 到 120 秒之间</span>
        <span v-if="modelInfo && !modelInfo.ready" class="chip warning"><Icon name="alert" :size="12" />{{ modelInfo.note }}</span>
        <span v-if="form.dub && dubDown" class="chip warning">
          <Icon name="alert" :size="12" />开始时会自动启动配音服务，约需 1.5–3 分钟
        </span>
        <span v-if="form.dub && services?.dub?.state === 'starting'" class="chip"><Icon name="spinner" :size="12" class="spin" />配音服务启动中</span>
        <span v-if="form.obs" class="chip warning"><Icon name="alert" :size="12" />推流前请确认你有权转播此内容，风险自负</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: 16px; padding: 12px 14px; flex-wrap: wrap; }
.bar.idle { flex-direction: column; align-items: stretch; gap: 10px; }
.bar.running { border-color: color-mix(in srgb, var(--onair) 35%, var(--border)); }
.row1 { display: flex; gap: 8px; }
.srcbox { flex: 1; position: relative; display: flex; align-items: center; }
.srcbox .ic { position: absolute; left: 11px; color: var(--ink-3); pointer-events: none; }
.srcbox .input { height: 40px; padding-left: 34px; font-size: 14px; }
.row1 > .btn { height: 40px; }
.go { min-width: 104px; }
.row2 { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 18px; font-size: 12.5px; color: var(--ink-2); }
.opt { display: inline-flex; align-items: center; gap: 6px; }
.opt .select { width: auto; height: 30px; font-size: 12.5px; max-width: 260px; }
.stepper { display: inline-flex; align-items: center; gap: 3px; }
.stepper .input { width: 46px; height: 28px; text-align: center; padding: 0 2px; -moz-appearance: textfield; }
.stepper .input::-webkit-inner-spin-button, .stepper .input::-webkit-outer-spin-button { -webkit-appearance: none; }
.switch { font-size: 12.5px; color: var(--ink-1); }
.hint { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; font-size: 12px; color: var(--ink-3); min-height: 24px; }
.hint > span { display: inline-flex; align-items: center; gap: 5px; }
.hint .chip .btn { height: 20px; margin-left: 4px; font-size: 11.5px; }

.tally { display: inline-flex; align-items: center; gap: 7px; height: 30px; padding: 0 12px; border-radius: 15px;
  font-weight: 600; font-size: 13px; background: var(--surface-2); color: var(--ink-1); flex-shrink: 0; }
.tally.running { background: var(--onair); color: #fff; }
.tally .dot { width: 8px; height: 8px; border-radius: 50%; background: #fff; animation: pulse 1.6s ease-in-out infinite; }
.tally .ic { color: var(--accent); }
.what { flex: 1; min-width: 0; }
.title { font-size: 15px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { margin-top: 3px; font-size: 12px; color: var(--ink-2); display: flex; flex-wrap: wrap; gap: 6px; white-space: nowrap; }
.meta i { font-style: normal; color: var(--ink-3); }
.steptext { display: inline-flex; align-items: center; gap: 2px; color: var(--ink-1); }
.stop { min-width: 104px; }
</style>
