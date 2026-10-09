<script setup lang="ts">
import Icon from "./Icon.vue";

// tone marks a value that needs a look; it always comes with an icon and a word (toneLabel), never color alone
defineProps<{ label: string; value: string; unit?: string; sub?: string; tone?: "warning" | "critical";
  toneLabel?: string; live?: boolean }>();
</script>

<template>
  <div class="kpi" :class="tone">
    <div class="lbl">
      <span>{{ label }}</span>
      <span v-if="tone" class="chip" :class="tone"><Icon :name="tone === 'critical' ? 'error' : 'alert'" :size="12" />{{ toneLabel }}</span>
    </div>
    <div class="val"><b :class="{ num: live }">{{ value }}</b><span v-if="unit" class="unit">{{ unit }}</span></div>
    <div v-if="sub" class="sub">{{ sub }}</div>
  </div>
</template>

<style scoped>
.kpi { min-width: 0; padding: 8px 11px 9px; border-radius: 8px; background: var(--surface-2); }
.kpi.warning { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 55%, transparent); }
.kpi.critical { box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--critical) 55%, transparent); }
.lbl { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 12px; color: var(--ink-2);
  min-height: 22px; }
.lbl .chip { height: 20px; padding: 0 6px; font-size: 11px; }
.val { display: flex; align-items: baseline; gap: 3px; margin-top: 2px; white-space: nowrap; }
.val b { font-size: 21px; font-weight: 600; line-height: 1.2; letter-spacing: -0.01em; }
.unit { font-size: 12px; color: var(--ink-2); }
.sub { margin-top: 2px; font-size: 11.5px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
