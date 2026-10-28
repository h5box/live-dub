<script setup lang="ts">
// One download (GET /api/downloads) where something is missing: a button with its size; while it runs, a progress bar
// with the speed and time left, its source (another can be picked mid-way: the download carries on from where it is)
// and pause; after a failure, the reason and a retry.
import { computed, ref, watch } from "vue";
import { size as fmt, span } from "../lib/format";
import { download, downloads, live, pauseDownload } from "../stores/live";
import Icon from "./Icon.vue";

const props = defineProps<{ id: string; size?: number }>();
const it = computed(() => live.downloadables[props.id]);
const job = computed(() => downloads.value[props.id]);
const total = computed(() => props.size ?? it.value?.size ?? 0);
const sources = computed(() => it.value?.sources ?? []);
const install = computed(() => !sources.value.length);   // an environment, set up by a script
// where from at the start: "" = the first that answers; a source picked by hand stays picked after a pause
const pick = ref("");
watch(() => (job.value && !job.value.auto ? job.value.source : ""), (s) => { pick.value = s; }, { immediate: true });
const state = computed(() => job.value?.state ?? "idle");
const pct = computed(() => job.value?.total ? Math.min(100, (100 * job.value.done) / job.value.total) : 0);
const left = computed(() => {
  const j = job.value;
  return j && j.speed > 0 && j.total > j.done ? span((j.total - j.done) / j.speed) : "";
});
const label = computed(() => state.value === "paused" ? "继续下载" : state.value === "failed" ? "重试"
  : `${install.value ? "安装" : "下载"}${total.value ? `（约 ${fmt(total.value)}）` : ""}`);
const switchTo = (e: Event) => download(props.id, (e.target as HTMLSelectElement).value);
</script>

<template>
  <div v-if="it" class="dl" :class="state">
    <template v-if="state === 'running' || state === 'queued'">
      <div class="line">
        <div class="bar" :class="{ busy: state === 'queued' || !job?.total }" role="progressbar" :aria-label="it.label"
             aria-valuemin="0" aria-valuemax="100" :aria-valuenow="job?.total ? Math.round(pct) : undefined">
          <i :style="job?.total ? { width: pct + '%' } : {}" />
        </div>
        <button v-if="job?.pausable" class="btn sm" @click="pauseDownload(id)">
          <Icon :name="state === 'queued' ? 'x' : 'pause'" :size="12" />{{ state === "queued" ? "取消" : "暂停" }}</button>
      </div>
      <div class="line">
        <span v-if="state === 'queued'" class="msg">排队中，前面的下载完成后开始</span>
        <span v-else-if="job?.message" class="msg">{{ job.message }}</span>
        <span v-else-if="job?.total" class="num">{{ fmt(job.done) }} / {{ fmt(job.total) }}<template v-if="job.speed">
          · {{ fmt(job.speed) }}/s</template><template v-if="left"> · 还需约 {{ left }}</template></span>
        <span class="sp" />
        <label v-if="sources.length > 1 && job" class="src">来源
          <select class="select" :value="job.source" @change="switchTo">
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.label }}</option>
          </select></label>
        <span v-else-if="job?.source && sources.length" class="muted">来自 {{ sources.find((s) => s.id === job?.source)?.label }}</span>
      </div>
    </template>
    <p v-else-if="state === 'done'" class="ok"><Icon name="check" :size="13" />{{ install ? "安装完成" : "下载完成" }}</p>
    <template v-else>
      <div class="line">
        <button class="btn sm primary" :disabled="!live.link" @click="download(id, pick)">
          <Icon :name="state === 'failed' ? 'restart' : 'download'" :size="13" />{{ label }}</button>
        <span v-if="state === 'paused' && job?.total" class="muted num">已下载 {{ fmt(job.done) }} / {{ fmt(job.total) }}</span>
        <label v-if="sources.length > 1" class="src">来源
          <select v-model="pick" class="select">
            <option value="">自动选择</option>
            <option v-for="s in sources" :key="s.id" :value="s.id">{{ s.label }}</option>
          </select></label>
      </div>
      <p v-if="state === 'failed'" class="err">{{ job?.error }}</p>
    </template>
  </div>
</template>

<style scoped>
.dl { display: flex; flex-direction: column; gap: 6px; max-width: 560px; }
.line { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 10px; min-height: 26px; font-size: 12px; color: var(--ink-2); }
.sp { flex: 1; }
.msg { color: var(--ink-2); }
.muted { color: var(--ink-3); }
.src { display: inline-flex; align-items: center; gap: 6px; color: var(--ink-3); }
/* the global .select is 36px tall; its arrow moves up with it */
.src .select { height: 26px; width: auto; max-width: 230px; padding-left: 8px; font-size: 12px;
  background-position: calc(100% - 15px) 10px, calc(100% - 10px) 10px; }
.bar { flex: 1; height: 6px; border-radius: 3px; background: var(--surface-3); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 3px; background: var(--accent); transition: width .5s; }
.bar.busy i { width: 30%; animation: slide 1.4s ease-in-out infinite; }
@keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(340%); } }
@media (prefers-reduced-motion: reduce) { .bar.busy i { animation: none; width: 100%; opacity: .4; } }
.ok { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--good-text); }
.err { font-size: 12px; line-height: 1.6; color: var(--critical); overflow-wrap: anywhere; }
</style>
