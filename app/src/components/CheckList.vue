<script setup lang="ts">
// Part of the environment check (GET /api/doctor): each item with what was found; what is missing gets its download
// button, or else how to fix it: a sentence with its commands in `backticks`, shown as code with a copy button.
import { ref } from "vue";
import type { DoctorItem } from "../api/types";
import { live } from "../stores/live";
import DownloadItem from "./DownloadItem.vue";
import Icon from "./Icon.vue";

defineProps<{ items: DoctorItem[] }>();
const parts = (fix: string) => fix.split("`").map((text, i) => ({ text, code: i % 2 === 1 })).filter((p) => p.text);
const copied = ref("");
async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    copied.value = text;
    setTimeout(() => { if (copied.value === text) copied.value = ""; }, 1500);
  } catch { /* the code stays selectable */ }
}
const downloadable = (it: DoctorItem) => !it.ok && !!it.install && !!live.downloadables[it.install];
</script>

<template>
  <ul class="checks">
    <li v-for="it in items" :key="it.label" :class="{ ok: it.ok, req: it.level === 'required' }">
      <Icon :name="it.ok ? 'check' : it.level === 'required' ? 'error' : 'alert'" :size="13" class="st" />
      <span class="lb">{{ it.label }}<span v-if="!it.ok && it.level !== 'required'" class="opt">可选</span></span>
      <span class="dt" :title="it.detail">{{ it.detail || (it.ok ? "" : "未找到") }}</span>
      <DownloadItem v-if="downloadable(it)" :id="it.install!" class="fix" />
      <p v-else-if="it.fix" class="fix">
        <template v-for="(p, i) in parts(it.fix)" :key="i">
          <span v-if="p.code" class="cmd"><code>{{ p.text }}</code><button class="cp" :title="copied === p.text ? '已复制' : '复制'"
                :aria-label="`复制 ${p.text}`" @click="copy(p.text)"><Icon :name="copied === p.text ? 'check' : 'copy'" :size="12" /></button></span>
          <template v-else>{{ p.text }}</template>
        </template>
      </p>
    </li>
  </ul>
</template>

<style scoped>
.checks { list-style: none; margin: 0; padding: 0; }
li { display: grid; grid-template-columns: 16px minmax(0, auto) minmax(0, 1fr); column-gap: 8px; row-gap: 5px;
  align-items: baseline; padding: 6px 0; font-size: 12.5px; }
li + li { border-top: 1px solid var(--border); }
.st { color: var(--warning); align-self: center; }
li.ok .st { color: var(--good); }
li.req:not(.ok) .st { color: var(--critical); }
.lb { color: var(--ink-1); }
.opt { margin-left: 6px; font-size: 11px; color: var(--ink-3); }
.dt { color: var(--ink-3); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fix { grid-column: 2 / -1; font-size: 12px; line-height: 1.6; color: var(--ink-2); overflow-wrap: anywhere; }
.cmd { white-space: nowrap; }
.cmd code { padding: 1px 5px; border-radius: 4px 0 0 4px; background: var(--surface-2); font-size: 11.5px; white-space: normal; }
.cp { display: inline-grid; place-items: center; width: 20px; height: 19px; vertical-align: bottom; border: 0;
  border-radius: 0 4px 4px 0; background: var(--surface-3); color: var(--ink-2); cursor: pointer; }
.cp:hover { color: var(--ink-1); }
</style>
