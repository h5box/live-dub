<script setup lang="ts">
import Icon from "./Icon.vue";

defineProps<{ tone: "warning" | "critical" | "info"; dismissible?: boolean }>();
defineEmits<{ dismiss: [] }>();
</script>

<template>
  <div class="banner" :class="tone" role="status">
    <Icon :name="tone === 'critical' ? 'error' : tone === 'warning' ? 'alert' : 'info'" :size="15" />
    <div class="body"><slot /></div>
    <button v-if="dismissible" class="btn ghost sm icon" title="关闭" @click="$emit('dismiss')"><Icon name="x" :size="13" /></button>
  </div>
</template>

<style scoped>
.banner { display: flex; align-items: flex-start; gap: 10px; padding: 9px 12px; border-radius: var(--radius);
  font-size: 13px; line-height: 1.5; border: 1px solid var(--border); background: var(--surface); }
.banner > .ic { margin-top: 2px; }
.banner.warning { background: var(--warning-soft); border-color: color-mix(in srgb, var(--warning) 35%, transparent); }
.banner.warning > .ic { color: var(--warning); }
.banner.critical { background: var(--critical-soft); border-color: color-mix(in srgb, var(--critical) 35%, transparent); }
.banner.critical > .ic { color: var(--critical); }
.banner.info > .ic { color: var(--accent); }
.body { flex: 1; min-width: 0; }
.body :deep(code) { padding: 1px 5px; border-radius: 4px; background: var(--surface-2); }
.banner .btn { margin: -3px -4px -3px 0; }
</style>
