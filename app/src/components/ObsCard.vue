<script setup lang="ts">
import { computed, ref } from "vue";
import { session } from "../stores/live";
import Icon from "./Icon.vue";

defineProps<{ compact?: boolean }>();
const url = computed(() => session.value?.obs_url ?? "rtmp://127.0.0.1:1935/live/livedub");
const copied = ref(false);
async function copy() {
  try { await navigator.clipboard.writeText(url.value); } catch {
    const t = document.createElement("textarea");
    t.value = url.value; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove();
  }
  copied.value = true;
  setTimeout(() => (copied.value = false), 1500);
}
</script>

<template>
  <button v-if="compact" class="btn obsbtn" :title="`OBS 媒体源地址：${url}（点击复制）`" @click="copy">
    <Icon :name="copied ? 'check' : 'obs'" :size="14" :class="{ okc: copied }" />{{ copied ? "已复制" : "OBS 地址" }}</button>
  <div v-else class="obs">
    <div class="url"><code :title="url">{{ url }}</code>
      <button class="btn sm" :class="{ ok: copied }" @click="copy">
        <Icon :name="copied ? 'check' : 'copy'" :size="13" />{{ copied ? "已复制" : "复制" }}</button>
    </div>
    <p class="hint">OBS 里加「媒体源」，取消勾选「本地文件」，填这个地址。每场都是同一个地址，设一次就行。</p>
  </div>
</template>

<style scoped>
.url { display: flex; align-items: center; gap: 8px; padding: 4px 4px 4px 10px; border-radius: var(--radius-sm);
  background: var(--surface-2); }
.url code { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.btn.ok .ic, .okc { color: var(--good); }
.obsbtn { flex-shrink: 0; }
.hint { margin-top: 8px; font-size: 12px; color: var(--ink-3); line-height: 1.55; }
</style>
