<script setup lang="ts">
// Settings of the app as a whole. For now: the download folder, where the ASR and translation models go; changing it
// moves the models downloaded so far (the backend stops the services that hold them open).
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { DownloadDir } from "../api/types";
import Banner from "../components/Banner.vue";
import Icon from "../components/Icon.vue";
import { size } from "../lib/format";
import { active, downloads, live } from "../stores/live";

const inTauri = "__TAURI_INTERNALS__" in window;
const dir = ref<DownloadDir | null>(null);
const editing = ref(false);          // without the desktop shell there is no folder dialog: type the path
const typed = ref("");
const msg = ref("");
const busy = ref(false);
const move = computed(() => downloads.value.move);
const moving = computed(() => ["queued", "running"].includes(move.value?.state ?? ""));
const wsl = computed(() => (live.svcInfo?.dub?.where ?? "").includes("WSL"));

async function refresh() {
  try { dir.value = await api.downloadDir(); } catch { /* not connected */ }
}
async function change() {
  msg.value = "";
  if (!inTauri) { typed.value = dir.value?.dir ?? ""; editing.value = true; return; }
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: true, defaultPath: dir.value?.dir, title: "选择下载位置" });
  if (typeof picked === "string") await apply(picked);
}
async function apply(path: string) {
  busy.value = true;
  msg.value = "";
  try {
    dir.value = await api.setDownloadDir(path);
    editing.value = false;
    live.status = await api.status();
  } catch (e) { msg.value = (e as Error).message; } finally { busy.value = false; }
}

watch(() => move.value?.state, (st) => { if (st === "done" || st === "failed") refresh(); });
onMounted(refresh);
</script>

<template>
  <div class="view">
    <header class="ph"><h1>设置</h1></header>

    <section class="card sec">
      <h2><Icon name="folder" :size="16" />下载位置</h2>
      <p class="what">识别模型（约 0.8 GB）和本地翻译模型（每个约 5 GB）下载后保存在这个文件夹，建议放在剩余空间较多的磁盘上。</p>
      <div v-if="!editing" class="path">
        <code class="p" :title="dir?.dir">{{ dir?.dir ?? "—" }}</code>
        <button class="btn" :disabled="!dir || busy || moving || active" :title="active ? '观看进行中，请先停止' : ''"
                @click="change">更改…</button>
      </div>
      <form v-else class="path" @submit.prevent="apply(typed)">
        <input v-model="typed" class="input" spellcheck="false" placeholder="例如 D:\LiveDub\models" aria-label="下载位置" />
        <button class="btn primary" :disabled="busy || !typed.trim()">保存</button>
        <button type="button" class="btn ghost" @click="editing = false">取消</button>
      </form>
      <p v-if="dir" class="meta num">已下载 {{ size(dir.used) }} · 所在磁盘剩余 {{ size(dir.free) }}</p>
      <div v-if="moving && move" class="gap">
        <div class="bar"><i :style="{ width: (move.total ? (100 * move.done) / move.total : 0) + '%' }" /></div>
        <p class="meta">{{ move.state === "queued" ? "等正在进行的下载完成后开始移动" : move.message || "正在移动…" }}
          <span v-if="move.total" class="num">{{ size(move.done) }} / {{ size(move.total) }}</span></p>
      </div>
      <Banner v-if="move?.state === 'failed'" tone="critical" class="gap">移动未完成：{{ move.error }}</Banner>
      <Banner v-if="msg" tone="critical" class="gap">{{ msg }}</Banner>
      <ul class="notes">
        <li>更改后，已下载的模型会一起移到新位置；移动期间识别和翻译服务会先停止。</li>
        <li v-if="wsl">配音环境安装在 WSL 中（约 20 GB），不在这个文件夹。</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.view { height: 100%; overflow-y: auto; padding: 20px 24px 28px; display: flex; flex-direction: column; gap: 10px; }
.ph { margin-bottom: 6px; }
.ph h1 { font-size: 20px; font-weight: 600; }
.sec { padding: 16px 18px 18px; max-width: 820px; }
.sec h2 { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; }
.sec h2 .ic { color: var(--ink-2); }
.what { margin-top: 6px; color: var(--ink-2); }
.path { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.path .p { flex: 1; min-width: 0; height: 36px; display: flex; align-items: center; padding: 0 12px;
  border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); font-size: 12.5px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.path .input { flex: 1; min-width: 0; }
.meta { margin-top: 8px; font-size: 12px; color: var(--ink-3); }
.gap { margin-top: 10px; }
.bar { height: 6px; border-radius: 3px; background: var(--surface-3); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 3px; background: var(--accent); transition: width .5s; }
.notes { margin: 12px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.7; color: var(--ink-3); }
</style>
