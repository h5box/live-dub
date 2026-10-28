<script setup lang="ts">
// The watch page's note on what must be added before a session can start (the required items of the environment
// check): one button downloads all of it, with the progress here; for translation, online is offered instead of a
// local model; what cannot be downloaded links to the environment page.
import { computed } from "vue";
import type { DoctorItem } from "../api/types";
import { size, span } from "../lib/format";
import { download, downloads, form, live } from "../stores/live";
import Banner from "./Banner.vue";
import Icon from "./Icon.vue";

const emit = defineEmits<{ navigate: [view: "services"] }>();
const isMt = (it: DoctorItem) => it.group === "翻译";
const missing = computed(() => (live.doctor?.items ?? []).filter((it) => it.level === "required" && !it.ok));
const gets = computed(() => [...new Set(missing.value.map((it) => it.install ?? "")
  .filter((k) => k && live.downloadables[k]))]);
const others = computed(() => missing.value.filter((it) => !it.install || !live.downloadables[it.install]));
const name = (k: string) => k.startsWith("model:") ? `翻译模型 ${live.downloadables[k].label}` : live.downloadables[k].label;
const bytes = (k: string) => live.models.find((m) => m.install === k)?.size || live.downloadables[k]?.size || 0;
const total = computed(() => gets.value.reduce((n, k) => n + bytes(k), 0));

// the downloads started from here (or from the cards), together; llama-server joins a translation model's
const jobs = computed(() => Object.entries(downloads.value)
  .filter(([k]) => gets.value.includes(k) || (k === "llama" && gets.value.some((g) => g.startsWith("model:")))));
const going = computed(() => jobs.value.some(([, j]) => j.state === "running" || j.state === "queued"));
const current = computed(() => jobs.value.find(([, j]) => j.state === "running")?.[1]);
const done = computed(() => jobs.value.reduce((n, [, j]) => n + (j.state === "done" ? j.total : j.done), 0));
const all = computed(() => Math.max(total.value, jobs.value.reduce((n, [, j]) => n + j.total, 0)));
const left = computed(() => current.value?.speed ? span((all.value - done.value) / current.value.speed) : "");
const failed = computed(() => jobs.value.filter(([, j]) => j.state === "failed"));
const getAll = () => { for (const k of gets.value) download(k); };
/** translation without a download: pick an online model and open its card to fill in the key */
function online() {
  const m = live.models.find((x) => x.online && x.ready) ?? live.models.find((x) => x.online);
  if (m) form.mt_model = m.key;
  emit("navigate", "services");
}
</script>

<template>
  <Banner v-if="gets.length && going" tone="info">
    <div class="row">
      <span>正在下载{{ current ? ` ${current.label}` : "" }}<span class="num"> · {{ size(done) }} / {{ size(all) }}<template
        v-if="current?.speed"> · {{ size(current.speed) }}/s</template><template v-if="left"> · 还需约 {{ left }}</template></span></span>
      <button class="btn sm" @click="emit('navigate', 'services')">查看详情</button>
    </div>
    <div class="bar"><i :style="{ width: (all ? (100 * done) / all : 0) + '%' }" /></div>
    <p class="sub">下载完成后即可开始观看，可以先离开此页面。</p>
  </Banner>
  <Banner v-else-if="gets.length" :tone="failed.length ? 'critical' : 'warning'">
    <div class="row">
      <span v-if="failed.length">下载未完成：{{ failed[0][1].error }}</span>
      <span v-else>开始观看前还需要下载：{{ gets.map(name).join("、") }}<span v-if="total" class="num">（共约 {{ size(total) }}）</span>。</span>
      <button class="btn sm primary" :disabled="!live.link" @click="getAll">
        <Icon :name="failed.length ? 'restart' : 'download'" :size="13" />{{ failed.length ? "重试" : "全部下载" }}</button>
    </div>
    <p v-if="gets.some((k) => k.startsWith('model:'))" class="sub">本地翻译需要 NVIDIA 显卡。不想下载翻译模型，也可以
      <button class="link" @click="online">改用线上翻译</button>（需要服务商的 API key）。</p>
  </Banner>
  <Banner v-for="it in others" :key="it.label" tone="warning">
    <div class="row">
      <span v-if="isMt(it)">还没有可用的翻译方式。没有 NVIDIA 显卡时，请使用线上翻译。</span>
      <span v-else>还缺少 {{ it.label }}。</span>
      <button v-if="isMt(it)" class="btn sm" @click="online">设置线上翻译</button>
      <button v-else class="btn sm" @click="emit('navigate', 'services')">查看处理方法</button>
    </div>
  </Banner>
</template>

<style scoped>
.row { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 12px; }
.row > span { flex: 1; min-width: 0; }
.bar { height: 5px; margin-top: 8px; border-radius: 3px; background: var(--surface-3); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 3px; background: var(--accent); transition: width .5s; }
.sub { margin-top: 4px; font-size: 12px; color: var(--ink-2); }
.link { padding: 0; border: 0; background: none; font: inherit; color: var(--accent); cursor: pointer; }
.link:hover { text-decoration: underline; }
</style>
