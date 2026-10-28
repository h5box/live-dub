<script setup lang="ts">
// Settings of the app as a whole. Storage: the download folder (the ASR and translation models; changing it moves the
// models downloaded so far, the backend stops the services that hold them open) and the sessions folder (how many
// sessions keep their video, and a clean-up of it). What closing the window does (desktop app only). The theme.
import { computed, onMounted, ref, watch } from "vue";
import { api } from "../api/client";
import type { DownloadDir, HistoryDir } from "../api/types";
import Banner from "../components/Banner.vue";
import Icon from "../components/Icon.vue";
import { size } from "../lib/format";
import { theme, type Theme } from "../lib/prefs";
import { active, downloads, live } from "../stores/live";

const inTauri = "__TAURI_INTERNALS__" in window;

// ---- the download folder ----------------------------------------------------------------------------------------
const dir = ref<DownloadDir | null>(null);
const editing = ref(false);          // without the desktop shell there is no folder dialog: type the path
const typed = ref("");
const msg = ref("");
const busy = ref(false);
const move = computed(() => downloads.value.move);
const moving = computed(() => ["queued", "running"].includes(move.value?.state ?? ""));
const wsl = computed(() => (live.svcInfo?.dub?.where ?? "").includes("WSL"));

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
watch(() => move.value?.state, async (st) => {
  if (st === "done" || st === "failed") dir.value = await api.downloadDir().catch(() => dir.value);
});

// ---- the sessions folder ----------------------------------------------------------------------------------------
const hist = ref<HistoryDir | null>(null);
const histMsg = ref("");
const confirming = ref(false);
const keeps = computed(() => [...new Set([1, 3, 5, 10, 20, hist.value?.keep ?? 5])].sort((a, b) => a - b));
async function setKeep(e: Event) {
  histMsg.value = "";
  try { hist.value = await api.setKeep(Number((e.target as HTMLSelectElement).value)); }
  catch (err) { histMsg.value = (err as Error).message; }
}
async function clean() {
  busy.value = true;
  histMsg.value = "";
  try {
    hist.value = await api.cleanHistory();
    confirming.value = false;
  } catch (e) { histMsg.value = (e as Error).message; } finally { busy.value = false; }
}

// ---- closing the window, the theme --------------------------------------------------------------------------------
const background = ref<boolean | null>(null);
async function setBackground(on: boolean) {
  try { background.value = (await api.setBackground(on)).run_in_background; }
  catch (e) { live.error = (e as Error).message; }
}
const THEMES: { id: Theme; label: string; icon: string }[] = [
  { id: "system", label: "跟随系统", icon: "auto" }, { id: "light", label: "浅色", icon: "sun" },
  { id: "dark", label: "深色", icon: "moon" }];
const setTheme = (t: Theme) => { theme.value = t; };

onMounted(async () => {
  [dir.value, hist.value, background.value] = await Promise.all([api.downloadDir().catch(() => null),
    api.historyDir().catch(() => null), api.background().then((b) => b.run_in_background).catch(() => null)]);
});
</script>

<template>
  <div class="view">
    <header class="ph"><h1>设置</h1></header>

    <section class="card sec">
      <h2><Icon name="folder" :size="16" />存储</h2>

      <div class="part">
        <h3>下载位置</h3>
        <p class="what">识别模型（约 0.8 GB）和本地翻译模型（每个约 5 GB）保存在这里，建议放在剩余空间较多的磁盘上。</p>
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
      </div>

      <div class="part">
        <h3>观看记录</h3>
        <p class="what">每一场的字幕、记录和视频缓存都在这里。视频缓存用于在「历史」中回放，占用的空间最多。</p>
        <div class="path"><code class="p" :title="hist?.dir">{{ hist?.dir ?? "—" }}</code></div>
        <p v-if="hist" class="meta num">共 {{ hist.count }} 场，占用 {{ size(hist.used) }}，其中视频缓存 {{ size(hist.media) }}
          · 所在磁盘剩余 {{ size(hist.free) }}</p>
        <div v-if="hist" class="row">
          <label class="keep">保留最近
            <select class="select" :value="hist.keep" aria-label="保留视频缓存的场数" @change="setKeep">
              <option v-for="n in keeps" :key="n" :value="n">{{ n }}</option>
            </select>
            场的视频缓存，更早的只保留字幕和记录</label>
          <span class="sp" />
          <button class="btn" :disabled="busy || !hist.media || confirming" @click="confirming = true">清理全部视频缓存</button>
        </div>
        <Banner v-if="confirming && hist" tone="warning" class="gap">
          <div class="confirm">
            <span>将删除{{ active ? "除进行中这一场外" : "" }}所有场次的视频和配音缓存（约 {{ size(hist.media) }}），之后不能在「历史」中回放；字幕和记录会保留。</span>
            <span class="acts"><button class="btn danger sm" :disabled="busy" @click="clean">确认清理</button>
              <button class="btn sm" @click="confirming = false">取消</button></span>
          </div>
        </Banner>
        <Banner v-if="histMsg" tone="critical" class="gap">{{ histMsg }}</Banner>
      </div>
    </section>

    <section v-if="inTauri && background !== null" class="card sec">
      <h2><Icon name="power" :size="16" />关闭窗口后</h2>
      <label class="switch gap"><input type="checkbox" :checked="background"
             @change="setBackground(($event.target as HTMLInputElement).checked)" />继续在后台运行识别、翻译和配音服务</label>
      <p class="what">{{ background
        ? "关闭窗口后服务继续运行：下次开始观看更快，但会一直占用显存。"
        : "关闭窗口时停止识别、翻译和配音等服务，释放显存；正在观看时，等这一场结束后再停止。" }}</p>
    </section>

    <section class="card sec">
      <h2><Icon name="sun" :size="16" />外观</h2>
      <div class="seg gap" role="radiogroup" aria-label="主题">
        <label v-for="t in THEMES" :key="t.id" :class="{ on: theme === t.id }">
          <input type="radio" name="theme" :checked="theme === t.id" @change="setTheme(t.id)" /><Icon :name="t.icon" :size="13" />{{ t.label }}</label>
      </div>
    </section>
  </div>
</template>

<style scoped>
.view { height: 100%; overflow-y: auto; padding: 20px 24px 28px; display: flex; flex-direction: column; gap: 12px; }
.ph { margin-bottom: 4px; }
.ph h1 { font-size: 20px; font-weight: 600; }
.sec { padding: 16px 18px 18px; max-width: 820px; }
.sec h2 { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; }
.sec h2 .ic { color: var(--ink-2); }
.part { margin-top: 12px; }
.part + .part { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); }
.part h3 { font-size: 13px; font-weight: 600; }
.what { margin-top: 4px; font-size: 12.5px; color: var(--ink-2); }
.path { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.path .p { flex: 1; min-width: 0; height: 36px; display: flex; align-items: center; padding: 0 12px;
  border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface-2); font-size: 12.5px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.path .input { flex: 1; min-width: 0; }
.meta { margin-top: 8px; font-size: 12px; color: var(--ink-3); }
.gap { margin-top: 10px; }
.bar { height: 6px; border-radius: 3px; background: var(--surface-3); overflow: hidden; }
.bar i { display: block; height: 100%; border-radius: 3px; background: var(--accent); transition: width .5s; }
.notes { margin: 10px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.7; color: var(--ink-3); }
.row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 10px; }
.sp { flex: 1; }
.keep { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.keep .select { width: auto; height: 30px; padding-left: 10px; background-position: calc(100% - 15px) 12px, calc(100% - 10px) 12px; }
.confirm { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; }
.confirm > span:first-child { flex: 1; min-width: 240px; }
.confirm .acts { display: inline-flex; gap: 6px; }
.switch { font-size: 13px; }
.sec .switch + .what { margin-top: 6px; }
</style>
