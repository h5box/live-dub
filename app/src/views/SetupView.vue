<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api } from "../api/client";
import type { SetupStatus } from "../api/types";
import { active, checkEnv, form, live, reloadModels, startSession } from "../stores/live";
import { save } from "../lib/prefs";

const emit = defineEmits<{ navigate: [view: "live" | "services"] }>();
const job = ref<SetupStatus | null>(null);
const modelDir = ref("");
const provider = ref("deepseek");
const key = ref("");
const address = ref("https://api.deepseek.com");
const model = ref("deepseek-flash");
const error = ref("");
const busy = ref(false);
const tested = ref(false);
const source = ref("");
const downloading = computed(() => job.value?.state === "running");
const ready = computed(() => live.doctor?.ok === true);
let timer = 0;
async function refresh() {
  try {
    const old = job.value?.state;
    job.value = await api.setupStatus();
    if (!modelDir.value) modelDir.value = job.value.model_dir ?? "";
    if (old === "running" && job.value.state !== "running") await Promise.all([checkEnv(), reloadModels()]);
  } catch { /* The backend connection banner handles reconnection. */ }
}
async function install(component: "subtitles" | "youtube" | "dub") {
  busy.value = true; error.value = "";
  try { job.value = await api.install(component, component === "subtitles" ? modelDir.value : undefined); }
  catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function connectProvider() {
  busy.value = true; tested.value = false; error.value = "";
  try {
    await api.saveProvider({ provider: provider.value, api_key: key.value || undefined,
      base_url: address.value, model: model.value });
    key.value = "";
    const result = await api.testProvider(provider.value);
    if (!result.ok) throw new Error(result.error);
    await Promise.all([checkEnv(), reloadModels()]);
    form.mt_model = provider.value === "deepseek" ? "deepseek-flash" : "custom";
    tested.value = true;
  } catch (e) { error.value = (e as Error).message; }
  finally { busy.value = false; }
}
async function testVideo() {
  form.dub = false; form.obs = false; form.src = source.value.trim() || job.value?.demo || "";
  await startSession({ ...form, delay: 10 });
  if (!live.error) finish();
  else error.value = live.error;
}
function finish() { save("setupDone", true); emit("navigate", "live"); }
onMounted(() => { refresh(); checkEnv(); timer = window.setInterval(refresh, 2000); });
onUnmounted(() => clearInterval(timer));
</script>

<template>
  <div class="setup view">
    <header><h1>开始使用 LiveDub</h1><p>先用 CPU 中文字幕，无需独立显卡。中文配音可以稍后安装。</p></header>
    <p v-if="!live.link" class="notice">正在连接本机服务…</p>
    <section class="card step">
      <h2>1. 准备字幕组件</h2>
      <p>下载英语识别和说话人模型（约 0.8 GB），缺少视频工具时一并安装。识别在本机运行，无需 WSL。</p>
      <label>模型存放目录<input v-model="modelDir" class="input" :disabled="downloading || active" /></label>
      <p class="muted">可放到容量充足的磁盘。模型遵循 CC BY 4.0，工具许可见项目的 THIRD_PARTY.md。</p>
      <div class="actions"><button class="btn primary" :disabled="!live.link || busy || downloading || active" @click="install('subtitles')">{{ job?.state === 'failed' ? '重试安装' : '安装 / 检查字幕组件' }}</button>
        <button class="btn" :disabled="!live.link || busy || downloading || active" @click="install('youtube')">安装 YouTube 播放组件</button></div>
      <p v-if="job?.message" role="status">{{ job.message }}</p>
      <p v-if="job?.error" class="error" role="alert">{{ job.error }}</p>
    </section>
    <section class="card step">
      <h2>2. 连接翻译</h2>
      <p>线上翻译会发送识别文本给你选择的接口，并按供应商价格计费。API key 只保存在本机。</p>
      <label>接口<select v-model="provider" class="select" @change="tested = false; address = provider === 'deepseek' ? 'https://api.deepseek.com' : ''; model = provider === 'deepseek' ? 'deepseek-flash' : ''"><option value="deepseek">DeepSeek</option><option value="custom">其他兼容接口</option></select></label>
      <label>接口地址<input v-model="address" class="input" @input="tested = false" /></label>
      <label>模型<input v-model="model" class="input" @input="tested = false" /></label>
      <label>API key<input v-model="key" class="input" type="password" autocomplete="off" placeholder="已保存时可留空" @input="tested = false" /></label>
      <div class="actions"><button class="btn primary" :disabled="!live.link || busy || !address || !model" @click="connectProvider">保存并测试</button><button class="btn" @click="emit('navigate', 'services')">使用本地翻译 / 高级设置</button><span v-if="tested">连接成功</span></div>
    </section>
    <section class="card step">
      <h2>3. 看第一段视频</h2>
      <p>{{ ready ? '字幕所需环境已就绪。' : '还需要：' + (live.doctor?.missing_required.join('、') || '等待环境检查') }}</p>
      <label>视频链接或本地路径<input v-model="source" class="input" :placeholder="job?.demo ? '留空播放内置英语测试片' : '输入一个英语视频链接或路径'" /></label>
      <div class="actions"><button class="btn primary" :disabled="!ready || active || busy || (!source && !job?.demo)" @click="testVideo">测试中文字幕</button><button class="btn" :disabled="!ready" @click="finish">完成设置，去观看</button></div>
    </section>
    <details class="card step"><summary>可选：安装原音色中文配音</summary><p>需要 NVIDIA 显卡、约 6.5 GB 空闲显存和约 20 GB 磁盘；Windows 需先安装 WSL 2。模型具有额外使用限制，见 THIRD_PARTY.md。首次启动可能需要 2–3 分钟。</p><button class="btn" :disabled="!live.link || downloading || busy || active" @click="install('dub')">安装配音组件</button></details>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.setup { height: 100%; overflow-y: auto; padding: 24px; }
header, .step, .notice, .error { max-width: 800px; margin: 0 auto 16px; }
header p { margin-top: 8px; color: var(--ink-2); }
.step { padding: 20px; }
h2 { font-size: 16px; margin-bottom: 8px; }
p { line-height: 1.7; margin-bottom: 12px; }
label { display: flex; align-items: center; gap: 12px; margin: 10px 0; white-space: nowrap; }
label .input { flex: 1; min-width: 0; }
label .select { flex: 1; min-width: 0; width: auto; }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.error { color: var(--critical); }
summary { cursor: pointer; font-weight: 600; }
</style>
