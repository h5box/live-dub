<script setup lang="ts">
// Compact readiness list for the live view: what a session needs and whether it is there.
import { computed } from "vue";
import type { SvcName } from "../api/types";
import { ICON, ORDER } from "../lib/serviceInfo";
import { active, focusModel, live, serviceAction, services } from "../stores/live";
import Icon from "./Icon.vue";
import SvcState from "./SvcState.vue";

defineEmits<{ manage: [] }>();

const online = computed(() => !!focusModel.value?.online);
const label = (key?: string | null) => live.models.find((m) => m.key === key)?.label ?? key ?? "";
function hint(n: SvcName): string {
  const s = services.value?.[n];
  if (!s) return "";
  if (n === "asr" && s.up) return s.boost?.length ? `热词 ${s.boost.length} 个` : "";
  if (n === "dub" && s.up) return [s.model, s.vram_mib ? `显存 ${(s.vram_mib / 1024).toFixed(1)} GB` : ""].filter(Boolean).join(" · ");
  if (n === "mt" && s.up) {   // a different model than the chosen one is swapped at the start
    const now = s.model ? label(s.model) : s.model_path ?? "";
    return focusModel.value && s.model !== focusModel.value.key ? `${now}，开始时换成 ${focusModel.value.label}` : now;
  }
  if (s.state !== "down") return "";
  if (n === "dub") return "需要配音时请提前启动";
  if (n === "mediamtx") return "输出到 OBS 时自动启动";
  return "开始观看时自动启动";
}
const canStart = (n: SvcName) => !live.busy && services.value?.[n]?.state === "down" && !(n === "mt" && online.value);
</script>

<template>
  <ul class="svcs">
    <li v-for="n in ORDER" :key="n">
      <Icon :name="ICON[n]" :size="15" class="si" />
      <span class="nm">{{ services?.[n]?.label ?? n }}</span>
      <template v-if="n === 'mt' && online">
        <span class="st-online"><Icon name="link" :size="13" />线上</span>
        <span class="hint">{{ focusModel?.label }}，{{ services?.mt?.up ? "本地翻译服务未使用" : "不占用本机资源" }}</span>
      </template>
      <template v-else>
        <SvcState :svc="services?.[n]" />
        <span class="hint" :title="services?.[n]?.error">{{ services?.[n]?.error || hint(n) }}</span>
      </template>
      <button v-if="canStart(n) && (n === 'dub' || !active)" class="btn sm" :disabled="!!live.busy"
              @click="serviceAction(n, 'start')">启动</button>
    </li>
  </ul>
  <button class="manage btn ghost sm" @click="$emit('manage')">环境与服务<Icon name="chevron" :size="12" /></button>
</template>

<style scoped>
.svcs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
li { display: grid; grid-template-columns: 18px 64px auto minmax(0, 1fr) auto; align-items: center; column-gap: 8px;
  min-height: 34px; }
li + li { border-top: 1px solid var(--border); }
.si { color: var(--ink-3); }
.nm { font-weight: 500; }
.hint { font-size: 11.5px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.st-online { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink-2); }
.st-online .ic { color: var(--accent); }
.manage { margin-top: 6px; margin-left: -8px; }
</style>
