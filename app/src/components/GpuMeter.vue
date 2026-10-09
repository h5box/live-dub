<script setup lang="ts">
import { computed } from "vue";
import { gb } from "../lib/format";
import { live } from "../stores/live";
import Icon from "./Icon.vue";

const g = computed(() => live.status?.gpu ?? {});
const pct = computed(() => (g.value.total_mb ? (100 * (g.value.used_mb ?? 0)) / g.value.total_mb : 0));
</script>

<template>
  <div class="gpu">
    <div class="row">
      <span class="lbl"><Icon name="chip" :size="14" />显存</span>
      <span v-if="g.total_mb" class="v num">{{ gb(g.used_mb) }} / {{ gb(g.total_mb) }} GB</span>
      <span v-else class="v muted">读不到（nvidia-smi）</span>
    </div>
    <div class="bar"><i :style="{ width: Math.min(100, pct) + '%' }" /></div>
    <div class="row sub">
      <span>GPU 占用 {{ g.util ?? "—" }}%</span>
      <span v-if="pct > 90" class="chip warning"><Icon name="alert" :size="12" />快满了</span>
    </div>
  </div>
</template>

<style scoped>
.gpu { display: flex; flex-direction: column; gap: 6px; }
.row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.lbl { display: inline-flex; align-items: center; gap: 6px; color: var(--ink-2); }
.lbl .ic { color: var(--ink-3); }
.v { font-size: 12px; }
.bar { height: 6px; border-radius: 3px; background: var(--surface-3); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 3px; background: var(--ink-2); transition: width .4s; }
.sub { font-size: 11.5px; color: var(--ink-3); }
</style>
