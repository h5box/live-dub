<script setup lang="ts">
// A service's state as icon + word
import type { Svc } from "../api/types";
import { STATE_LABEL } from "../lib/serviceInfo";
import Icon from "./Icon.vue";

defineProps<{ svc?: Svc | null }>();
</script>

<template>
  <span class="st" :class="svc?.error ? 'err' : svc?.state ?? 'down'" :title="svc?.error">
    <Icon v-if="svc?.error" name="error" :size="13" />
    <Icon v-else-if="svc?.state === 'starting' || svc?.state === 'stopping'" name="spinner" :size="12" class="spin" />
    <Icon v-else-if="svc?.up" name="check" :size="13" />
    <i v-else class="off" />
    {{ svc?.error ? "出错" : STATE_LABEL[svc?.state ?? "down"] }}
  </span>
</template>

<style scoped>
.st { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink-2); white-space: nowrap; }
.st.up .ic { color: var(--good); }
.st.starting .ic, .st.stopping .ic { color: var(--accent); }
.st.err { color: var(--ink-1); }
.st.err .ic { color: var(--critical); }
.off { width: 8px; height: 8px; margin: 0 2px; border-radius: 50%; border: 1.5px solid var(--ink-3); }
</style>
