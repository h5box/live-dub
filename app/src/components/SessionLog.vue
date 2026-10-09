<script setup lang="ts">
import { computed, ref } from "vue";
import { hms } from "../lib/format";
import Icon from "./Icon.vue";

const props = withDefaults(defineProps<{ log: [number, string][]; max?: number; busy?: boolean }>(), { max: 6 });
const all = ref(false);
const shown = computed(() => (all.value ? props.log : props.log.slice(-props.max)));
const bad = (m: string) => /失败|出错|起不来|错误|不在/.test(m);
</script>

<template>
  <div class="log">
    <button v-if="log.length > max" class="btn ghost sm more" @click="all = !all">
      {{ all ? "收起" : `显示全部 ${log.length} 条` }}</button>
    <ol>
      <li v-for="(l, i) in shown" :key="i" :class="{ bad: bad(l[1]), last: busy && i === shown.length - 1 }">
        <span class="t num">{{ hms(l[0]) }}</span>
        <span class="m"><Icon v-if="bad(l[1])" name="error" :size="12" class="badic" />{{ l[1] }}</span>
      </li>
    </ol>
    <p v-if="!log.length" class="none">还没有记录</p>
  </div>
</template>

<style scoped>
ol { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
li { display: grid; grid-template-columns: 58px 1fr; gap: 8px; font-size: 12px; line-height: 1.5; color: var(--ink-2); }
li .t { color: var(--ink-3); }
li .m { word-break: break-all; }
li.bad .m { color: var(--ink-1); }
.badic { display: inline-block; vertical-align: -2px; margin-right: 4px; color: var(--critical); }
li.last .m::after { content: " …"; animation: pulse 1.2s infinite; }
.more { margin: -4px 0 6px -8px; }
.none { font-size: 12px; color: var(--ink-3); }
</style>
