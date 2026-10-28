<script setup lang="ts">
// One resident service on the environment page: where it runs, its port and logs, what the environment check found
// for it, start / restart / stop, and the last log lines on demand.
import { computed, ref } from "vue";
import { api } from "../api/client";
import type { DoctorItem, SvcName } from "../api/types";
import { active, live, serviceAction, services } from "../stores/live";
import Banner from "./Banner.vue";
import CheckList from "./CheckList.vue";
import Icon from "./Icon.vue";

const props = withDefaults(defineProps<{ name: SvcName; checks?: DoctorItem[]; startable?: boolean }>(), { startable: true });
const svc = computed(() => services.value?.[props.name]);
const info = computed(() => live.svcInfo?.[props.name]);
const settling = computed(() => ["starting", "stopping"].includes(svc.value?.state ?? ""));
const busy = computed(() => live.busy.startsWith(props.name + ":"));
const label = (key?: string | null) => live.models.find((m) => m.key === key)?.label ?? key ?? "";

const log = ref<Record<string, string> | null>(null);
async function toggleLog() {
  if (log.value) { log.value = null; return; }
  try { log.value = await api.serviceLog(props.name, 120); } catch (e) { live.error = (e as Error).message; }
}
</script>

<template>
  <div class="cols">
    <dl>
      <dt>运行在</dt><dd>{{ info?.where ?? "—" }}</dd>
      <dt>端口</dt><dd class="num">{{ info?.port ?? "—" }}</dd>
      <dt>启动用时</dt><dd>{{ info?.boot ?? "—" }}</dd>
      <template v-if="name === 'mt' && svc?.up"><dt>已加载</dt><dd>{{ svc.model ? label(svc.model) : svc.model_path }}</dd></template>
      <template v-if="name === 'dub' && svc?.up">
        <dt>模型</dt><dd>{{ svc.model ?? "—" }}</dd>
        <template v-if="svc.vram_mib"><dt>显存</dt><dd class="num">{{ (svc.vram_mib / 1024).toFixed(1) }} GB</dd></template>
        <template v-if="svc.impl"><dt>实现</dt><dd class="impl">{{ svc.impl }}</dd></template>
      </template>
      <dt>日志</dt><dd class="logs"><code v-for="l in info?.logs ?? []" :key="l">{{ l }}</code></dd>
    </dl>
    <div v-if="checks?.length" class="chk">
      <h3>组件</h3>
      <CheckList :items="checks" />
    </div>
  </div>
  <div v-if="name === 'asr' && svc?.boost?.length" class="boost">
    <span class="k">热词</span><span v-for="b in svc.boost" :key="b" class="tag">{{ b }}</span>
  </div>
  <Banner v-if="svc?.error" tone="critical" class="err">{{ svc.error }}</Banner>
  <div class="acts">
    <template v-if="svc?.up">
      <button class="btn sm" :disabled="!!live.busy || active || settling" :title="active ? '观看进行中，请先停止' : ''"
              @click="serviceAction(name, 'restart')"><Icon name="restart" :size="13" />重启</button>
      <button class="btn sm" :disabled="!!live.busy || active || settling" :title="active ? '观看进行中，请先停止' : ''"
              @click="serviceAction(name, 'stop')"><Icon name="power" :size="13" />停止</button>
    </template>
    <button v-else-if="startable" class="btn sm" :disabled="!!live.busy || settling || !live.link"
            @click="serviceAction(name, 'start')">
      <Icon :name="busy ? 'spinner' : 'play'" :size="12" :class="{ spin: busy }" />启动</button>
    <button class="btn ghost sm" :aria-expanded="!!log" @click="toggleLog">
      <Icon name="log" :size="13" />{{ log ? "收起日志" : "查看最近日志" }}</button>
  </div>
  <div v-if="log" class="logbox">
    <template v-for="(text, f) in log" :key="f">
      <div class="lf">{{ f }}</div>
      <pre>{{ text || "（暂无内容）" }}</pre>
    </template>
  </div>
</template>

<style scoped>
.cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px 32px; margin-top: 12px; }
@container (max-width: 760px) { .cols { grid-template-columns: minmax(0, 1fr); } }
dl { display: grid; grid-template-columns: 72px 1fr; gap: 6px 10px; margin: 0; font-size: 12.5px; align-content: start; }
dt { color: var(--ink-3); }
dd { margin: 0; color: var(--ink-1); min-width: 0; overflow-wrap: anywhere; }
.logs { display: flex; flex-wrap: wrap; gap: 4px 8px; }
.impl { font-size: 12px; color: var(--ink-2); }
.chk h3 { font-size: 12px; font-weight: 500; color: var(--ink-3); margin-bottom: 2px; }
.boost { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; font-size: 12.5px; }
.boost .k { color: var(--ink-3); width: 72px; }
.tag { padding: 1px 8px; border-radius: 4px; background: var(--surface-2); font-size: 12px; color: var(--ink-2); }
.err { margin-top: 12px; }
.acts { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
.acts .ghost { margin-left: auto; }
.logbox { margin-top: 8px; }
.lf { font-size: 11.5px; color: var(--ink-3); margin: 6px 0 2px; }
.logbox pre { max-height: 260px; overflow: auto; margin: 0; padding: 8px 10px; border-radius: var(--radius-sm);
  background: var(--surface-2); font-size: 11.5px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
