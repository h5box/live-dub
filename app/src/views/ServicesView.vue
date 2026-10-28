<script setup lang="ts">
// The environment page: one folding card per part of the pipeline (识别, 翻译, 配音, OBS 输出), then sign-ins and
// this machine. A card's header says where the part runs and its state; its body picks local or online, shows the
// service (start / stop, port, logs) and what the environment check found for it, with a download for each gap.
import { computed, onMounted, reactive, ref, watch } from "vue";
import Banner from "../components/Banner.vue";
import CheckList from "../components/CheckList.vue";
import DownloadItem from "../components/DownloadItem.vue";
import Fold from "../components/Fold.vue";
import GpuMeter from "../components/GpuMeter.vue";
import Icon from "../components/Icon.vue";
import SvcPanel from "../components/SvcPanel.vue";
import SvcState from "../components/SvcState.vue";
import { api, BASE } from "../api/client";
import type { DoctorItem, MtModel, Provider, SvcName, YoutubeAuth } from "../api/types";
import { load, save } from "../lib/prefs";
import { active, APP_VERSION, checkEnv, focusModel, form, live, nextModel, reloadModels, serviceAction, services,
  staleBackend } from "../stores/live";

const svc = (n: SvcName) => services.value?.[n];
const label = (key?: string | null) => live.models.find((m) => m.key === key)?.label ?? key ?? "";

// ---- the environment check, split over the cards -------------------------------------------------------------
type Part = "asr" | "mt" | "dub" | "obs" | "login" | "pc";
const isCookies = (it: DoctorItem) => it.group === "YouTube 播放" && it.label.includes("cookies");
const PART_OF: Record<Part, (it: DoctorItem) => boolean> = {
  asr: (it) => it.group === "识别",
  mt: () => false,                                   // each translation option says itself whether it can run
  dub: (it) => it.group.startsWith("配音"),
  obs: (it) => it.group.startsWith("OBS"),
  login: isCookies,
  pc: (it) => ["基础", "显卡"].includes(it.group) || (it.group === "YouTube 播放" && !isCookies(it)),
};
const checks = (p: Part) => (live.doctor?.items ?? []).filter(PART_OF[p]);
const lacks = (p: Part, level?: string) => checks(p).filter((it) => !it.ok && (!level || it.level === level)).length;
/** what a card's header says is missing: something to download, or something to install by hand */
const missing = (p: Part, level?: string) =>
  checks(p).some((it) => !it.ok && (!level || it.level === level) && !it.install) ? "缺少组件" : "需要下载";
const canGet = (m: MtModel) => !!m.install && !!live.downloadables[m.install];
/** a service starts once what it runs on is there (OBS output can go without the subtitle font) */
const startable = (p: Part) => !checks(p).some((it) => !it.ok && (p !== "obs" || it.label === "MediaMTX"));

// a card opens by itself when its part needs attention, until it is folded or opened by hand (remembered)
const opened = reactive<Partial<Record<Part, boolean>>>(load("envOpen", {}));
watch(opened, () => save("envOpen", { ...opened }), { deep: true });
const SVC: Partial<Record<Part, SvcName>> = { asr: "asr", mt: "mt", dub: "dub", obs: "mediamtx" };
function attention(p: Part): boolean {
  if (p === "mt") return !!focusModel.value && !focusModel.value.ready && !active.value;
  if (p === "pc") return lacks("pc", "required") > 0 || staleBackend.value !== null;
  const n = SVC[p];
  return lacks(p, "required") > 0 || !!(n && svc(n)?.error);
}
const isOpen = (p: Part) => opened[p] ?? attention(p);

// ---- translation: one choice, the same one as the watch page's ----------------------------------------------
const localModels = computed(() => live.models.filter((m) => !m.online));
const onlineModels = computed(() => live.models.filter((m) => m.online));
const picked = computed(() => live.models.find((m) => m.key === nextModel.value));
const mtMode = computed(() => (picked.value?.online ? "online" : "local"));
const lastPick: Record<string, string> = {};
function setMode(mode: "local" | "online") {
  if (mode === mtMode.value) return;
  if (picked.value) lastPick[mtMode.value] = picked.value.key;
  const list = mode === "online" ? onlineModels.value : localModels.value;
  const m = list.find((x) => x.key === lastPick[mode]) ?? list.find((x) => x.ready) ?? list[0];
  if (m) form.mt_model = m.key;
}
const pick = (m: MtModel) => { form.mt_model = m.key; };
/** the local translation service against the pick: loaded, swapped at the start, or started then */
const localNote = computed(() => {
  const s = svc("mt"), m = picked.value;
  if (!m || m.online || active.value) return "";
  if (!s?.up) return "开始观看时自动启动翻译服务并加载所选模型。";
  return s.model && s.model !== m.key ? `翻译服务正在运行 ${label(s.model)}，开始观看时会换成 ${m.label}。` : "";
});

// the online interfaces: the form of the one picked
const providers = ref<Provider[]>([]);
const edit = reactive<Record<string, { base_url: string; model: string; api_key: string }>>({});
const test = reactive<Record<string, string>>({});
const saving = ref("");
const prov = computed(() => providers.value.find((p) => p.name === picked.value?.provider));
async function loadProviders() {
  try {
    providers.value = await api.providers();
    for (const p of providers.value) edit[p.name] = { base_url: p.base_url, model: p.model, api_key: "" };
  } catch { /* not connected */ }
}
async function saveProvider(p: Provider) {
  saving.value = p.name;
  test[p.name] = "";
  try {
    const e = edit[p.name];
    providers.value = await api.saveProvider({ provider: p.name, base_url: e.base_url, model: e.model,
      api_key: e.api_key || undefined });
    e.api_key = "";
    await Promise.all([reloadModels(), checkEnv()]);
    test[p.name] = "已保存";
  } catch (err) { test[p.name] = (err as Error).message; } finally { saving.value = ""; }
}
async function testProvider(p: Provider) {
  saving.value = p.name;
  test[p.name] = "测试中…";
  try {
    const r = await api.testProvider(p.name);
    test[p.name] = r.ok ? `测试通过：「${r.reply}」（${r.wall_s} 秒）` : `测试失败：${r.error}`;
  } catch (err) { test[p.name] = (err as Error).message; } finally { saving.value = ""; }
}

// ---- sign-ins ---------------------------------------------------------------------------------------------------
const BROWSERS: Record<string, string> = { firefox: "Firefox", edge: "Edge", chrome: "Chrome", brave: "Brave",
  chromium: "Chromium", opera: "Opera", vivaldi: "Vivaldi" };   // the backend also takes a profile, e.g. firefox:work
const yt = ref<YoutubeAuth | null>(null);
const ytMode = ref("");   // "" none, "file" a cookies.txt, else a browser
const ytFile = ref("");
const ytMsg = ref("");
const ytSaving = ref(false);
const ytSet = computed(() => !!yt.value && !!(yt.value.cookies || yt.value.cookies_from_browser));
const ytLost = computed(() => !!yt.value?.cookies && !yt.value.cookies_found);
function showYoutube(a: YoutubeAuth) {
  yt.value = a;
  ytMode.value = a.cookies ? "file" : a.cookies_from_browser;
  ytFile.value = a.cookies;
}
async function loadYoutube() {
  try { showYoutube(await api.youtube()); } catch { /* not connected */ }
}
async function saveYoutube() {
  ytSaving.value = true;
  ytMsg.value = "";
  try {
    const file = ytMode.value === "file";
    showYoutube(await api.saveYoutube({ cookies: file ? ytFile.value : "", cookies_from_browser: file ? "" : ytMode.value }));
    ytMsg.value = "已保存，下一场开始时生效";
  } catch (err) { ytMsg.value = (err as Error).message; } finally { ytSaving.value = false; }
}

onMounted(() => { checkEnv(); loadProviders(); loadYoutube(); });
</script>

<template>
  <div class="view">
    <header class="ph">
      <div>
        <h1>环境</h1>
        <p>每一部分用本地还是线上、是否就绪。缺少的组件在对应卡片里点「下载」即可。</p>
      </div>
      <button class="btn sm" :disabled="live.checking" @click="checkEnv">
        <Icon name="restart" :size="13" :class="{ spin: live.checking }" />重新检查</button>
    </header>
    <Banner v-if="live.error" tone="critical" dismissible @dismiss="live.error = ''">{{ live.error }}</Banner>

    <Fold id="f-asr" icon="mic" title="识别" sub="把英文语音转成文字，并区分说话人" :open="isOpen('asr')"
          @update:open="opened.asr = $event">
      <template #aside>
        <span class="where">本地</span>
        <span v-if="lacks('asr', 'required')" class="chip warning"><Icon name="alert" :size="12" />{{ missing("asr", "required") }}</span>
        <SvcState v-else :svc="svc('asr')" />
      </template>
      <div class="mode">
        <span class="ml">使用</span>
        <div class="seg" role="radiogroup" aria-label="识别方式">
          <label class="on"><input type="radio" name="asr-mode" checked />本地</label>
          <label class="na" title="暂不支持线上识别"><input type="radio" name="asr-mode" disabled />线上<small>暂不支持</small></label>
        </div>
      </div>
      <p class="what">{{ live.svcInfo?.asr?.what }}</p>
      <SvcPanel name="asr" :checks="checks('asr')" :startable="startable('asr')" />
    </Fold>

    <Fold id="f-mt" icon="translate" title="翻译" sub="把识别出的英文译成中文字幕" :open="isOpen('mt')"
          @update:open="opened.mt = $event">
      <template #aside>
        <template v-if="focusModel">
          <span class="where">{{ focusModel.online ? "线上" : "本地" }} · {{ focusModel.label }}</span>
          <span v-if="focusModel.online" class="chip" :class="focusModel.ready || active ? 'good' : 'warning'">
            <Icon :name="focusModel.ready || active ? 'check' : 'alert'" :size="12" />{{ focusModel.ready || active ? "可用" : "未配置" }}</span>
          <span v-else-if="!focusModel.ready && !active" class="chip warning"><Icon name="alert" :size="12" />{{
            canGet(focusModel) ? "需要下载" : "不可用" }}</span>
          <SvcState v-else :svc="svc('mt')" />
        </template>
      </template>
      <p v-if="active && focusModel" class="note"><Icon name="info" :size="13" />本场使用 {{ focusModel.label }}；在这里改选，从下一场开始生效。</p>
      <div class="mode">
        <span class="ml">使用</span>
        <div class="seg" role="radiogroup" aria-label="翻译方式">
          <label :class="{ on: mtMode === 'local' }">
            <input type="radio" name="mt-mode" :checked="mtMode === 'local'" @change="setMode('local')" />本地</label>
          <label :class="{ on: mtMode === 'online' }">
            <input type="radio" name="mt-mode" :checked="mtMode === 'online'" @change="setMode('online')" />线上</label>
        </div>
        <span class="hint">与「观看」页高级选项中的「翻译」是同一个设置</span>
      </div>

      <template v-if="mtMode === 'local'">
        <p class="what">在本机显卡上运行（llama-server），约占 5 GB 显存，不产生费用。</p>
        <div class="opts" role="radiogroup" aria-label="本地翻译模型">
          <div v-for="m in localModels" :key="m.key" class="opt" :class="{ on: m.key === nextModel }">
            <label class="pick">
              <input type="radio" name="mt-local" :checked="m.key === nextModel" @change="pick(m)" />
              <span class="nm">{{ m.label }}<span class="dt">{{ m.detail }}</span></span>
              <span class="st" :class="{ ok: m.ready }"><Icon :name="m.ready ? 'check' : 'alert'" :size="12" />{{
                m.ready ? "可用" : canGet(m) ? "未下载" : "不可用" }}</span>
            </label>
            <p v-if="!m.ready && (m.install === 'llama' || !canGet(m))" class="why">{{ m.note }}</p>
            <DownloadItem v-if="canGet(m)" :id="m.install!" :size="m.size || undefined" class="get" />
          </div>
        </div>
        <p v-if="localNote" class="hint gap">{{ localNote }}</p>
        <SvcPanel name="mt" :startable="!!picked?.ready" />
      </template>

      <template v-else>
        <p class="what">翻译交给线上接口（DeepSeek 或任何 OpenAI 兼容接口），不占显存，按服务商的价格计费。API key 只保存在本机的
          <code>.secret/</code> 目录。</p>
        <div class="opts" role="radiogroup" aria-label="线上翻译接口">
          <label v-for="m in onlineModels" :key="m.key" class="opt" :class="{ on: m.key === nextModel }">
            <span class="pick">
              <input type="radio" name="mt-online" :checked="m.key === nextModel" @change="pick(m)" />
              <span class="nm">{{ m.label }}<span class="dt">{{ m.detail }}</span></span>
              <span class="st" :class="{ ok: m.ready }"><Icon :name="m.ready ? 'check' : 'alert'" :size="12" />{{ m.ready ? "可用" : "未配置" }}</span>
            </span>
          </label>
        </div>
        <div v-if="prov" class="form">
          <div class="fh2">
            <h3>{{ prov.label }} 接口</h3>
            <a v-if="prov.key_url" :href="prov.key_url" target="_blank" rel="noreferrer" class="muted">申请 API key</a>
          </div>
          <label class="field"><span>接口地址</span>
            <input v-model="edit[prov.name].base_url" class="input" spellcheck="false" placeholder="https://…/v1" /></label>
          <label class="field"><span>模型</span>
            <input v-model="edit[prov.name].model" class="input" spellcheck="false"
                   :placeholder="prov.name === 'deepseek' ? '留空则使用 deepseek-flash' : '例如 qwen-plus、gpt-4.1-mini'" /></label>
          <label class="field"><span>API key</span>
            <input v-model="edit[prov.name].api_key" class="input" type="password" autocomplete="off"
                   :placeholder="prov.has_key ? '已保存，留空则不修改' : '粘贴 API key'" /></label>
          <div class="acts">
            <button class="btn sm primary" :disabled="saving === prov.name" @click="saveProvider(prov)">保存</button>
            <button class="btn sm" :disabled="saving === prov.name || !!prov.problem" @click="testProvider(prov)">测试</button>
            <span class="muted tres" role="status">{{ test[prov.name] }}</span>
          </div>
        </div>
        <div v-if="svc('mt')?.up" class="note idle">
          <Icon name="info" :size="13" />
          <span>本地翻译服务仍在运行（{{ label(svc("mt")?.model) || "llama-server" }}），线上翻译用不到它，停止后可释放约 5 GB 显存。</span>
          <button class="btn sm" :disabled="!!live.busy || active" @click="serviceAction('mt', 'stop')">
            <Icon name="power" :size="13" />停止</button>
        </div>
      </template>
    </Fold>

    <Fold id="f-dub" icon="voice" title="配音" sub="用原说话人的音色朗读中文译文（可选）" :open="isOpen('dub')"
          @update:open="opened.dub = $event">
      <template #aside>
        <span class="where">本地</span>
        <span v-if="lacks('dub')" class="chip"><Icon name="alert" :size="12" />{{ missing("dub") === "需要下载" ? "未安装" : "缺少组件" }}</span>
        <SvcState v-else :svc="svc('dub')" />
      </template>
      <div class="mode">
        <span class="ml">使用</span>
        <div class="seg" role="radiogroup" aria-label="配音方式">
          <label class="on"><input type="radio" name="dub-mode" checked />本地</label>
          <label class="na" title="暂不支持线上配音"><input type="radio" name="dub-mode" disabled />线上<small>暂不支持</small></label>
        </div>
      </div>
      <p class="what">{{ live.svcInfo?.dub?.what }}。开始观看时勾选「中文配音」才会用到；启动约需 1.5–3 分钟，建议提前启动。</p>
      <SvcPanel name="dub" :checks="checks('dub')" :startable="startable('dub')" />
    </Fold>

    <Fold id="f-obs" icon="stream" title="OBS 输出" sub="把带字幕和配音的画面推流给 OBS（可选）" :open="isOpen('obs')"
          @update:open="opened.obs = $event">
      <template #aside>
        <span class="where">本地</span>
        <span v-if="lacks('obs')" class="chip"><Icon name="alert" :size="12" />{{ missing("obs") }}</span>
        <SvcState v-else :svc="svc('mediamtx')" />
      </template>
      <p class="what top">{{ live.svcInfo?.mediamtx?.what }}。开启「输出到 OBS」时自动启动。</p>
      <SvcPanel name="mediamtx" :checks="checks('obs')" :startable="startable('obs')" />
    </Fold>

    <Fold id="f-login" icon="key" title="登录" sub="视频网站要求登录验证时使用" :open="isOpen('login')"
          @update:open="opened.login = $event">
      <template #aside>
        <span v-if="ytLost" class="chip warning"><Icon name="alert" :size="12" />YouTube：找不到 cookies 文件</span>
        <span v-else-if="ytSet" class="chip good"><Icon name="check" :size="12" />YouTube 已设置</span>
        <span v-else class="chip">未设置</span>
      </template>
      <p class="what top">读取链接时带上你在浏览器中的登录状态。这里只保存文件路径或浏览器名称，Cookie 只在本机使用，不会上传。</p>
      <div class="site">
        <h3>YouTube</h3>
        <div class="form">
          <label class="field"><span>方式</span>
            <select v-model="ytMode" class="select">
              <option value="">不使用</option>
              <option value="file">导出的 cookies.txt</option>
              <option v-for="(name, b) in BROWSERS" :key="b" :value="b">从 {{ name }} 读取</option>
              <option v-if="yt?.cookies_from_browser && !(yt.cookies_from_browser in BROWSERS)"
                      :value="yt.cookies_from_browser">从 {{ yt.cookies_from_browser }} 读取</option>
            </select></label>
          <label v-if="ytMode === 'file'" class="field"><span>文件路径</span>
            <input v-model="ytFile" class="input" spellcheck="false" placeholder="例如 D:\Downloads\cookies.txt" /></label>
          <p v-else-if="ytMode" class="hint">需先在该浏览器中登录 YouTube。Edge、Chrome 运行时 Cookie 文件被占用，新版还可能无法解密，建议用
            Firefox 或导出的 cookies.txt。</p>
          <div class="acts">
            <button class="btn sm primary" :disabled="ytSaving || (ytMode === 'file' && !ytFile.trim())" @click="saveYoutube">保存</button>
            <span class="muted tres" role="status">{{ ytMsg }}</span>
          </div>
        </div>
      </div>
    </Fold>

    <Fold id="f-pc" icon="plug" title="本机" sub="显卡、后端和基础组件" :open="isOpen('pc')" @update:open="opened.pc = $event">
      <template #aside>
        <span v-if="live.status?.version" class="where">后端 {{ live.status.version }}</span>
        <span v-if="!live.link" class="chip critical"><Icon name="error" :size="12" />后端未连接</span>
        <span v-else-if="staleBackend !== null" class="chip warning"><Icon name="alert" :size="12" />后端版本不一致</span>
        <span v-else-if="lacks('pc', 'required')" class="chip warning"><Icon name="alert" :size="12" />{{ missing("pc", "required") }}</span>
        <span v-else class="chip good"><Icon name="check" :size="12" />已连接</span>
      </template>
      <div class="pc">
        <div>
          <h3>显卡</h3>
          <p v-if="live.status?.gpu?.name" class="what">{{ live.status.gpu.name }}</p>
          <GpuMeter class="gapt" />
          <p class="hint gapt">本地翻译约占用 5 GB 显存，配音约 6.5 GB。其他程序同时使用显卡时，可能显存不足或互相拖慢。</p>
          <Banner v-for="w in live.status?.warnings ?? []" :key="w" tone="warning" class="gapt">{{ w }}</Banner>
        </div>
        <div>
          <h3>后端</h3>
          <p class="what">窗口只负责界面，服务的启停和观看的开始、停止都由后端完成。关闭窗口后，进行中的观看会继续；没在观看时，各项服务会停止以释放显存（可在「设置」中更改）。</p>
          <dl>
            <dt>地址</dt><dd><code>{{ BASE || "http://127.0.0.1:8090" }}</code></dd>
            <template v-if="live.status"><dt>版本</dt>
              <dd>{{ live.status.version ?? "旧版本（未报告版本号）" }}<span v-if="live.status.version !== APP_VERSION" class="warn">，与界面（{{ APP_VERSION }}）不一致</span></dd></template>
            <dt>手动启动</dt><dd><code>uv run python -m livedub.server</code></dd>
            <dt>命令行检查</dt><dd><code>uv run python -m livedub.doctor</code></dd>
            <template v-if="live.doctor"><dt>设置文件</dt><dd><code>{{ live.doctor.settings_file }}</code>{{
              live.doctor.settings_exists ? "" : "（不存在，正在使用默认值）" }}</dd></template>
          </dl>
        </div>
        <div class="span">
          <h3>基础组件</h3>
          <CheckList :items="checks('pc')" />
        </div>
      </div>
    </Fold>
  </div>
</template>

<style scoped>
.view { height: 100%; overflow-y: auto; padding: 20px 24px 28px; display: flex; flex-direction: column; gap: 10px;
  container-type: inline-size; }
.ph { display: flex; align-items: flex-end; gap: 16px; margin-bottom: 6px; }
.ph > div { flex: 1; min-width: 0; }
.ph h1 { font-size: 20px; font-weight: 600; }
.ph p { margin-top: 4px; color: var(--ink-2); }

/* header summary: where the part runs, then its state */
.where { font-size: 12px; color: var(--ink-2); padding: 1px 8px; border: 1px solid var(--border-2); border-radius: 10px;
  white-space: nowrap; max-width: 260px; overflow: hidden; text-overflow: ellipsis; }

.what { color: var(--ink-2); margin-top: 10px; }
.what.top { margin-top: 12px; }
.hint { font-size: 12px; line-height: 1.6; color: var(--ink-3); }
.hint.gap { margin-top: 8px; }
.gapt { margin-top: 10px; }
.note { display: flex; align-items: center; gap: 8px; margin-top: 12px; padding: 7px 10px; border-radius: var(--radius-sm);
  background: var(--accent-soft); font-size: 12.5px; }
.note > .ic { color: var(--accent); flex: none; }
.note.idle { background: var(--surface-2); max-width: 760px; }
.note.idle > span { flex: 1; }

/* local or online: a segmented radio group */
.mode { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; margin-top: 12px; }
.ml { font-size: 12.5px; color: var(--ink-3); }

/* the models or interfaces of the chosen side: one row each, the picked one marked */
.opts { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; max-width: 760px; }
.opt { display: flex; flex-direction: column; gap: 6px; padding: 9px 12px; border: 1px solid var(--border);
  border-radius: var(--radius-sm); }
.pick { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; align-items: center; column-gap: 10px; cursor: pointer; }
.opt:hover { border-color: var(--border-2); }
.opt.on { border-color: var(--accent); background: var(--accent-soft); }
.opt input { appearance: none; margin: 0; width: 16px; height: 16px; border-radius: 50%; border: 1.5px solid var(--ink-3);
  display: grid; place-items: center; cursor: pointer; }
.opt input:checked { border-color: var(--accent); }
.opt input:checked::after { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.opt .nm { font-weight: 600; min-width: 0; }
.opt .dt { margin-left: 10px; font-weight: 400; font-size: 12px; color: var(--ink-3); }
.opt .st { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink-2); }
.opt .st .ic { color: var(--warning); }
.opt .st.ok .ic { color: var(--good); }
.opt .why, .opt .get { margin-left: 28px; }
.opt .why { font-size: 12px; color: var(--ink-3); overflow-wrap: anywhere; }
label.opt { cursor: pointer; }

.form { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; max-width: 760px; }
.fh2 { display: flex; align-items: center; gap: 8px; }
.fh2 h3, .site h3, .pc h3 { font-size: 12.5px; font-weight: 600; }
.fh2 a { font-size: 12px; margin-left: auto; }
.form .field { display: grid; grid-template-columns: 64px 1fr; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-3); }
.form .input { height: 30px; font-size: 12.5px; }
/* the global .select is 36px tall; its arrow moves up with it */
.form .select { height: 30px; font-size: 12.5px; background-position: calc(100% - 15px) 12px, calc(100% - 10px) 12px; }
.acts { display: flex; align-items: center; gap: 8px; padding-top: 6px; }
.tres { font-size: 12px; overflow-wrap: anywhere; }
.site { margin-top: 14px; }
.site .form { margin-top: 6px; }

.pc { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px 32px; margin-top: 12px; }
.pc .span { grid-column: 1 / -1; }
.pc h3 + .what { margin-top: 4px; }
@container (max-width: 760px) { .pc { grid-template-columns: minmax(0, 1fr); } }
dl { display: grid; grid-template-columns: 72px 1fr; gap: 6px 10px; margin: 10px 0 0; font-size: 12.5px; }
dt { color: var(--ink-3); }
dd { margin: 0; color: var(--ink-1); min-width: 0; overflow-wrap: anywhere; }
dd .warn { color: var(--warning); }
</style>
