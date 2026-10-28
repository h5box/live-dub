<script setup lang="ts">
import { computed } from "vue";
import { gb } from "../lib/format";
import { live, onAir, session } from "../stores/live";
import Icon from "./Icon.vue";

export type View = "live" | "history" | "services" | "settings";
const view = defineModel<View>({ required: true });

const items: { id: View; label: string; icon: string }[] = [
  { id: "live", label: "观看", icon: "live" },
  { id: "history", label: "历史", icon: "history" },
  { id: "services", label: "环境", icon: "services" },
  { id: "settings", label: "设置", icon: "sliders" },
];

const gpu = computed(() => live.status?.gpu ?? {});
const gpuPct = computed(() => gpu.value.total_mb ? Math.min(100, (100 * (gpu.value.used_mb ?? 0)) / gpu.value.total_mb) : 0);
const svcTrouble = computed(() => Object.values(live.status?.services ?? {}).some((s) => s.error) ||
  (live.doctor !== null && !live.doctor.ok));
const starting = computed(() => ["starting", "stopping"].includes(session.value?.state ?? ""));
</script>

<template>
  <nav class="rail">
    <div class="mark" title="声影同传 · 外语直播实时翻译">
      <img src="/logo.svg" width="36" height="36" alt="声影同传" />
    </div>

    <button v-for="it in items" :key="it.id" class="item" :class="{ on: view === it.id }"
            :aria-current="view === it.id ? 'page' : undefined" @click="view = it.id">
      <span class="ico">
        <Icon :name="it.icon" :size="20" />
        <i v-if="it.id === 'live' && onAir" class="badge air" title="正在翻译" />
        <i v-else-if="it.id === 'live' && starting" class="badge busy" />
        <i v-else-if="it.id === 'services' && svcTrouble" class="badge bad" title="环境缺少组件，或有服务出错" />
      </span>
      <span class="lbl">{{ it.label }}</span>
    </button>

    <div class="sp" />

    <div v-if="gpu.total_mb" class="gpu" :title="`显存 ${gb(gpu.used_mb)} / ${gb(gpu.total_mb)} GB · GPU 占用 ${gpu.util ?? '—'}%`">
      <span class="lbl">显存</span>
      <span class="bar"><i :style="{ width: gpuPct + '%' }" /></span>
      <span class="val num">{{ gb(gpu.used_mb) }}G</span>
    </div>

    <div class="link" :class="{ off: !live.link }" :title="live.link ? '已连接后端 127.0.0.1:8090' : '无法连接后端 127.0.0.1:8090'">
      <Icon :name="live.link ? 'plug' : 'alert'" :size="14" />
      <span>{{ live.link ? "已连接" : "未连接" }}</span>
    </div>
  </nav>
</template>

<style scoped>
.rail { width: 72px; flex-shrink: 0; display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 14px 0 10px; border-right: 1px solid var(--border); background: var(--plane); }
.mark { width: 36px; height: 36px; margin-bottom: 14px; }
.mark img { display: block; }
.item { width: 60px; display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 7px 0 6px;
  border: 0; border-radius: 8px; background: transparent; color: var(--ink-2); cursor: pointer; font: inherit; }
.item:hover { background: var(--surface-2); color: var(--ink-1); }
.item.on { background: var(--accent-soft); color: var(--accent); }
.item.on .lbl { color: var(--ink-1); font-weight: 600; }
.ico { position: relative; display: grid; place-items: center; width: 24px; height: 24px; }
.lbl { font-size: 11.5px; line-height: 1.2; }
.badge { position: absolute; top: -1px; right: -3px; width: 9px; height: 9px; border-radius: 50%;
  border: 2px solid var(--plane); }
.badge.air { background: var(--onair); animation: pulse 1.6s ease-in-out infinite; }
.badge.busy { background: var(--warning); }
.badge.bad { background: var(--critical); }
.sp { flex: 1; }
.gpu { width: 56px; display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 6px 0; }
.gpu .lbl { color: var(--ink-3); font-size: 11px; }
.gpu .bar { width: 40px; height: 4px; border-radius: 2px; background: var(--surface-3); overflow: hidden; }
.gpu .bar i { display: block; height: 100%; background: var(--ink-2); border-radius: 2px; }
.gpu .val { font-size: 11px; color: var(--ink-2); }
.link { display: flex; flex-direction: column; align-items: center; gap: 2px; font-size: 11px; color: var(--ink-3);
  padding: 4px 0 6px; }
.link .ic { color: var(--good); }
.link.off { color: var(--ink-1); }
.link.off .ic { color: var(--critical); }
</style>
