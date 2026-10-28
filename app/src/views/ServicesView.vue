<script setup lang="ts">
// The environment page: what this machine still lacks (the backend's environment check, with the command that fixes
// each gap), then three sections of cards: the resident services (start / stop, where they run, their logs), the
// settings (online translation keys, YouTube sign-in), this machine (GPU, backend).
import { computed, onMounted, reactive, ref } from "vue";
import Banner from "../components/Banner.vue";
import GpuMeter from "../components/GpuMeter.vue";
import Icon from "../components/Icon.vue";
import SvcState from "../components/SvcState.vue";
import { api, BASE } from "../api/client";
import type { DoctorItem, Provider, SvcName, YoutubeAuth } from "../api/types";
import { ICON, ORDER } from "../lib/serviceInfo";
import { active, APP_VERSION, checkEnv, focusPreset, live, reloadModels, serviceAction, services } from "../stores/live";

const busy = (n: SvcName) => live.busy.startsWith(n + ":");
const online = computed(() => !!focusPreset.value?.mt_online);
const svc = (n: SvcName) => services.value?.[n];
const info = (n: SvcName) => live.svcInfo?.[n];
const settling = (n: SvcName) => ["starting", "stopping"].includes(svc(n)?.state ?? "");

// ---- environment check ---------------------------------------------------------------------------------------
const groups = computed(() => {
  const out: { name: string; items: DoctorItem[] }[] = [];
  for (const it of live.doctor?.items ?? []) {
    let g = out.find((x) => x.name === it.group);
    if (!g) out.push((g = { name: it.group, items: [] }));
    g.items.push(it);
  }
  return out;
});
const missingReq = computed(() => live.doctor?.missing_required ?? []);
const showAll = ref(false);
const groupOk = (items: DoctorItem[]) => items.every((i) => i.ok || i.level !== "required");
// what a group lacks. In a group with required items (翻译: at least one way to translate) the optional ones are
// alternatives, so once the required ones are met an alternative that is not set up is not something missing
function misses(items: DoctorItem[]) {
  const met = items.some((i) => i.level === "required") && groupOk(items);
  return items.filter((i) => !i.ok && !(met && i.level !== "required"));
}
// with only what is missing shown, the groups that lack nothing fold into one line so the rest stands out
const complete = computed(() => groups.value.filter((g) => !misses(g.items).length));
const listed = computed(() => (showAll.value ? groups.value : groups.value.filter((g) => !complete.value.includes(g))));
const shown = (it: DoctorItem, items: DoctorItem[]) => showAll.value || misses(items).includes(it);
/** a fix hint: plain text with the commands in `backticks` */
const fixParts = (fix: string) => fix.split("`").map((text, i) => ({ text, code: i % 2 === 1 })).filter((p) => p.text);

// ---- logs ------------------------------------------------------------------------------------------------------
const logs = reactive<Partial<Record<SvcName, Record<string, string> | null>>>({});
async function toggleLog(n: SvcName) {
  if (logs[n]) { logs[n] = null; return; }
  try { logs[n] = await api.serviceLog(n, 120); } catch (e) { live.error = (e as Error).message; }
}

// ---- online translation ----------------------------------------------------------------------------------------
const providers = ref<Provider[]>([]);
const edit = reactive<Record<string, { base_url: string; model: string; api_key: string }>>({});
const test = reactive<Record<string, string>>({});
const saving = ref("");
// one provider's form at a time: the first one set up, else the first
const provTab = ref("");
const prov = computed(() => providers.value.find((p) => p.name === provTab.value) ?? providers.value[0]);
async function loadProviders() {
  try {
    providers.value = await api.providers();
    for (const p of providers.value) edit[p.name] = { base_url: p.base_url, model: p.model, api_key: "" };
    if (!providers.value.some((p) => p.name === provTab.value)) {
      provTab.value = (providers.value.find((p) => !p.problem) ?? providers.value[0])?.name ?? "";
    }
  } catch { /* not connected */ }
}
// tabs pattern, as in the other views: arrows / Home / End move between the providers
function provKey(e: KeyboardEvent) {
  const names = providers.value.map((p) => p.name);
  const i = names.indexOf(prov.value?.name ?? "");
  const keys: Record<string, string> = { ArrowLeft: names[(i + names.length - 1) % names.length],
    ArrowRight: names[(i + 1) % names.length], Home: names[0], End: names[names.length - 1] };
  const next = keys[e.key];
  if (!next || e.ctrlKey || e.metaKey || e.altKey) return;
  e.preventDefault();
  e.stopPropagation();
  provTab.value = next;
  document.getElementById(`prov-tab-${next}`)?.focus();
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

// ---- YouTube sign-in -------------------------------------------------------------------------------------------
const BROWSERS: Record<string, string> = { firefox: "Firefox", edge: "Edge", chrome: "Chrome", brave: "Brave",
  chromium: "Chromium", opera: "Opera", vivaldi: "Vivaldi" };   // the backend also takes a profile, e.g. firefox:work
const yt = ref<YoutubeAuth | null>(null);
const ytMode = ref("");   // "" none, "file" a cookies.txt, else a browser
const ytFile = ref("");
const ytMsg = ref("");
const ytSaving = ref(false);
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
      <h1>环境</h1>
      <p>查看本机缺少的组件和各服务的状态、日志，设置线上翻译和 YouTube 登录。</p>
    </header>
    <Banner v-if="live.error" tone="critical" dismissible @dismiss="live.error = ''">{{ live.error }}</Banner>

    <section class="card env">
      <header class="card-h">
        <span class="si"><Icon name="check" :size="16" /></span><h2>环境检查</h2>
        <span v-if="live.doctor" class="chip" :class="live.doctor.ok ? 'good' : 'critical'">
          <Icon :name="live.doctor.ok ? 'check' : 'alert'" :size="12" />
          {{ live.doctor.ok ? "必需项已就绪" : `缺少 ${missingReq.length} 个必需项` }}</span>
        <span class="sp" />
        <label class="switch sm"><input v-model="showAll" type="checkbox" />显示全部检查项</label>
        <button class="btn sm" :disabled="live.checking" @click="checkEnv">
          <Icon name="restart" :size="13" :class="{ spin: live.checking }" />重新检查</button>
      </header>
      <div class="card-b">
        <p v-if="!live.doctor" class="muted">{{ live.checking ? "检查中…" : "连接后端后自动检查" }}</p>
        <div v-for="g in listed" :key="g.name" class="grp">
          <h3><Icon :name="groupOk(g.items) ? 'check' : 'alert'" :size="13" :class="groupOk(g.items) ? 'ok' : 'bad'" />{{ g.name }}</h3>
          <template v-for="it in g.items" :key="it.label">
            <div v-if="shown(it, g.items)" class="it" :class="{ ok: it.ok, req: it.level === 'required' }">
              <Icon :name="it.ok ? 'check' : it.level === 'required' ? 'error' : 'info'" :size="14" class="st" />
              <span class="lb">{{ it.label }}<span v-if="!it.ok && it.level !== 'required'" class="opt">可选</span></span>
              <span class="dt" :title="it.detail">{{ it.detail }}</span>
              <p v-if="it.fix" class="fix"><template v-for="(part, i) in fixParts(it.fix)" :key="i"><code v-if="part.code">{{ part.text }}</code><template v-else>{{ part.text }}</template></template></p>
            </div>
          </template>
        </div>
        <p v-if="!showAll && complete.length" class="allok">
          <Icon name="check" :size="13" class="ok" />已就绪：{{ complete.map((g) => g.name).join("、") }}</p>
        <p v-if="live.doctor" class="settings muted">设置文件：<code>{{ live.doctor.settings_file }}</code>
          {{ live.doctor.settings_exists ? "" : "（文件不存在，正在使用默认值；如需修改路径或端口，可参照 settings.example.json 创建）" }}</p>
      </div>
    </section>

    <section class="sec" aria-labelledby="sec-svc">
      <div class="sec-h">
        <h2 id="sec-svc">服务</h2>
        <p>开始观看时会自动启动识别和本地翻译（开启「输出到 OBS」时还会启动 MediaMTX）；配音服务启动约需 1.5–3 分钟，建议提前启动。观看进行中不能停止服务。</p>
      </div>
      <div class="grid svcs">
        <article v-for="n in ORDER" :key="n" class="card">
          <header class="card-h">
            <span class="si"><Icon :name="ICON[n]" :size="16" /></span>
            <h3>{{ svc(n)?.label ?? n }}</h3>
            <SvcState :svc="svc(n)" />
            <span class="sp" />
            <template v-if="svc(n)?.up">
              <button class="btn sm" :disabled="!!live.busy || active || settling(n)" :title="active ? '观看进行中，请先停止' : ''"
                      @click="serviceAction(n, 'restart')"><Icon name="restart" :size="13" />重启</button>
              <button class="btn sm" :disabled="!!live.busy || active || settling(n)" :title="active ? '观看进行中，请先停止' : ''"
                      @click="serviceAction(n, 'stop')"><Icon name="power" :size="13" />停止</button>
            </template>
            <button v-else-if="!(n === 'mt' && online)" class="btn sm" :disabled="!!live.busy || settling(n) || !live.link"
                    @click="serviceAction(n, 'start')">
              <Icon :name="busy(n) ? 'spinner' : 'play'" :size="12" :class="{ spin: busy(n) }" />启动</button>
          </header>
          <div class="card-b">
            <p class="what">{{ info(n)?.what }}</p>
            <p v-if="n === 'mt' && online" class="note"><Icon name="link" :size="13" />
              {{ active ? "本场" : "当前场景" }}使用线上翻译 {{ focusPreset?.mt_model }}，无需此服务。</p>
            <dl>
              <dt>运行在</dt><dd>{{ info(n)?.where }}</dd>
              <dt>端口</dt><dd class="num">{{ info(n)?.port }}</dd>
              <dt>启动用时</dt><dd>{{ info(n)?.boot }}</dd>
              <template v-if="n === 'mt' && svc(n)?.up"><dt>模型</dt><dd>{{ svc(n)?.model ?? svc(n)?.model_path }}</dd></template>
              <template v-if="n === 'dub' && svc(n)?.up">
                <dt>模型</dt><dd>{{ svc(n)?.model ?? "—" }}</dd>
                <template v-if="svc(n)?.vram_mib"><dt>显存</dt><dd class="num">{{ ((svc(n)?.vram_mib ?? 0) / 1024).toFixed(1) }} GB</dd></template>
                <template v-if="svc(n)?.impl"><dt>实现</dt><dd class="impl">{{ svc(n)?.impl }}</dd></template>
              </template>
              <dt>日志</dt>
              <dd class="logs"><code v-for="l in info(n)?.logs ?? []" :key="l">{{ l }}</code></dd>
            </dl>
            <div v-if="n === 'asr' && svc(n)?.boost?.length" class="boost">
              <span class="k">热词</span>
              <span v-for="b in svc(n)?.boost" :key="b" class="tag">{{ b }}</span>
            </div>
            <Banner v-if="svc(n)?.error" tone="critical" class="err">{{ svc(n)?.error }}</Banner>
            <div class="foot">
              <button class="btn ghost sm" :aria-expanded="!!logs[n]" @click="toggleLog(n)">
                <Icon name="log" :size="13" />{{ logs[n] ? "收起日志" : "查看最近日志" }}</button>
            </div>
            <div v-if="logs[n]" class="logbox">
              <template v-for="(text, f) in logs[n]" :key="f">
                <div class="lf">{{ f }}</div>
                <pre>{{ text || "（暂无内容）" }}</pre>
              </template>
            </div>
          </div>
        </article>
      </div>
    </section>

    <section class="sec" aria-labelledby="sec-set">
      <div class="sec-h"><h2 id="sec-set">设置</h2></div>
      <div class="grid two">
        <article class="card">
          <header class="card-h"><span class="si"><Icon name="key" :size="16" /></span><h3>线上翻译</h3></header>
          <div class="card-b">
            <p class="what">适合没有 NVIDIA 显卡或想节省显存时使用：识别仍在本机进行，翻译交给线上接口（DeepSeek 或任何 OpenAI 兼容接口）。
              API key 只保存在本机的 <code>.secret/</code> 目录。</p>
            <div v-if="providers.length" class="seg" role="tablist" aria-label="线上翻译接口" @keydown="provKey">
              <button v-for="p in providers" :id="`prov-tab-${p.name}`" :key="p.name" role="tab"
                      :aria-controls="`prov-panel-${p.name}`" :aria-selected="p.name === prov?.name"
                      :tabindex="p.name === prov?.name ? 0 : -1" :class="{ on: p.name === prov?.name }"
                      @click="provTab = p.name">
                <span class="dot" :class="p.problem ? 'warn' : 'good'" aria-hidden="true" />{{ p.label }}</button>
            </div>
            <div v-if="prov" :id="`prov-panel-${prov.name}`" class="form" role="tabpanel" :aria-labelledby="`prov-tab-${prov.name}`">
              <div class="ph2">
                <span class="chip" :class="prov.problem ? 'warning' : 'good'">
                  <Icon :name="prov.problem ? 'alert' : 'check'" :size="12" />{{ prov.problem ? "未配置" : "已配置" }}</span>
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
          </div>
        </article>

        <article class="card">
          <header class="card-h">
            <span class="si"><Icon name="link" :size="16" /></span><h3>YouTube 登录</h3>
            <span v-if="yt && (yt.cookies || yt.cookies_from_browser)" class="chip" :class="yt.cookies && !yt.cookies_found ? 'warning' : 'good'">
              <Icon :name="yt.cookies && !yt.cookies_found ? 'alert' : 'check'" :size="12" />{{ yt.cookies && !yt.cookies_found ? "找不到 cookies 文件" : "已设置" }}</span>
          </header>
          <div class="card-b">
            <p class="what">YouTube 提示需要登录验证时使用：读取链接时带上你在浏览器中的登录状态。这里只保存文件路径或浏览器名称，Cookie
              只在本机使用，不会上传。</p>
            <div class="form">
              <label class="field"><span>方式</span>
                <select v-model="ytMode" class="select">
                  <option value="">不使用</option>
                  <option value="file">导出的 cookies.txt</option>
                  <option v-for="(label, b) in BROWSERS" :key="b" :value="b">从 {{ label }} 读取</option>
                  <option v-if="yt?.cookies_from_browser && !(yt.cookies_from_browser in BROWSERS)"
                          :value="yt.cookies_from_browser">从 {{ yt.cookies_from_browser }} 读取</option>
                </select></label>
              <label v-if="ytMode === 'file'" class="field"><span>文件路径</span>
                <input v-model="ytFile" class="input" spellcheck="false" placeholder="例如 D:\Downloads\cookies.txt" /></label>
              <p v-else-if="ytMode" class="hint">需先在该浏览器中登录 YouTube。Edge、Chrome 运行时 Cookie 文件被占用，新版还可能无法解密，建议用 Firefox
                或导出的 cookies.txt。</p>
              <div class="acts">
                <button class="btn sm primary" :disabled="ytSaving || (ytMode === 'file' && !ytFile.trim())" @click="saveYoutube">保存</button>
                <span class="muted tres" role="status">{{ ytMsg }}</span>
              </div>
            </div>
          </div>
        </article>
      </div>
    </section>

    <section class="sec" aria-labelledby="sec-pc">
      <div class="sec-h"><h2 id="sec-pc">本机</h2></div>
      <div class="grid two">
        <article class="card">
          <header class="card-h"><span class="si"><Icon name="chip" :size="16" /></span><h3>显卡</h3></header>
          <div class="card-b">
            <p v-if="live.status?.gpu?.name" class="what">{{ live.status.gpu.name }}</p>
            <GpuMeter />
            <p class="what gapt">本地翻译约占用 5 GB 显存，配音约 6.5 GB。其他程序同时使用显卡时，可能显存不足或互相拖慢。</p>
            <Banner v-for="w in live.status?.warnings ?? []" :key="w" tone="warning" class="err">{{ w }}</Banner>
          </div>
        </article>

        <article class="card">
          <header class="card-h">
            <span class="si"><Icon name="plug" :size="16" /></span><h3>后端</h3>
            <span class="state" :class="{ off: !live.link }">
              <Icon :name="live.link ? 'check' : 'error'" :size="13" />{{ live.link ? "已连接" : "未连接" }}</span>
          </header>
          <div class="card-b">
            <p class="what">窗口只负责界面，服务的启停和观看的开始、停止都由后端完成。关闭窗口后，后端和进行中的翻译会继续运行。</p>
            <dl>
              <dt>地址</dt><dd><code>{{ BASE || "http://127.0.0.1:8090" }}</code></dd>
              <template v-if="live.status"><dt>版本</dt>
                <dd>{{ live.status.version ?? "旧版本（未报告版本号）" }}<span v-if="live.status.version !== APP_VERSION" class="warn">，与界面（{{ APP_VERSION }}）不一致</span></dd></template>
              <dt>手动启动</dt><dd><code>uv run python -m livedub.server</code></dd>
              <dt>命令行检查</dt><dd><code>uv run python -m livedub.doctor</code></dd>
            </dl>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.view { height: 100%; overflow-y: auto; padding: 20px 24px 28px; display: flex; flex-direction: column; gap: 14px;
  container-type: inline-size; }
.ph h1 { font-size: 20px; font-weight: 600; }
.ph p { margin-top: 4px; color: var(--ink-2); max-width: 820px; }

/* three sections of cards. A row's cards share one height (the grid stretches them) and keep their actions at the
   bottom, so no card leaves a hole beside a taller one */
.sec { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }
.sec-h h2 { font-size: 14px; font-weight: 600; }
.sec-h p { margin-top: 2px; font-size: 12.5px; color: var(--ink-3); max-width: 820px; }
.grid { display: grid; gap: var(--gap); }
.svcs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@container (min-width: 1240px) { .svcs { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
@container (max-width: 700px) { .svcs, .two { grid-template-columns: minmax(0, 1fr); } }
.grid > .card { display: flex; flex-direction: column; min-width: 0; }
.grid > .card > .card-b { flex: 1; display: flex; flex-direction: column; }   /* .foot sits at its bottom */
.card-h { gap: 10px; }
.card-h h3 { font-size: 13px; font-weight: 600; white-space: nowrap; }
.sp { flex: 1; }
.si { width: 28px; height: 28px; border-radius: 7px; display: grid; place-items: center; background: var(--surface-2);
  color: var(--ink-2); flex: none; }
.what { color: var(--ink-2); }
.gapt { margin-top: 12px; }
.note { display: flex; align-items: center; gap: 6px; margin-top: 8px; padding: 6px 10px; border-radius: var(--radius-sm);
  background: var(--accent-soft); font-size: 12.5px; }
.note .ic { color: var(--accent); }
dl { display: grid; grid-template-columns: 72px 1fr; gap: 6px 10px; margin: 12px 0 0; font-size: 12.5px; }
dt { color: var(--ink-3); }
dd { margin: 0; color: var(--ink-1); min-width: 0; overflow-wrap: anywhere; }
dd .warn { color: var(--warning); }
.logs { display: flex; flex-wrap: wrap; gap: 4px 8px; }
.impl { font-size: 12px; color: var(--ink-2); }
.boost { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; }
.boost .k { font-size: 12.5px; color: var(--ink-3); width: 72px; }
.tag { padding: 1px 8px; border-radius: 4px; background: var(--surface-2); font-size: 12px; color: var(--ink-2); }
.err { margin-top: 12px; }
.foot { margin-top: auto; padding-top: 10px; }
.foot .btn { margin-left: -8px; }
.logbox { margin-top: 6px; }
.lf { font-size: 11.5px; color: var(--ink-3); margin: 6px 0 2px; }
.logbox pre { max-height: 260px; overflow: auto; margin: 0; padding: 8px 10px; border-radius: var(--radius-sm);
  background: var(--surface-2); font-size: 11.5px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
.state { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink-2); }
.state .ic { color: var(--good); }
.state.off .ic { color: var(--critical); }

.env .card-h { gap: 10px; }
.env .card-b { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 12px 24px; }
.switch.sm { font-size: 12px; color: var(--ink-2); }
.grp h3 { display: flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; margin-bottom: 4px; }
.grp h3 .ok, .it.ok .st { color: var(--good); }
.grp h3 .bad { color: var(--warning); }
.it { display: grid; grid-template-columns: 16px minmax(0, auto) minmax(0, 1fr); column-gap: 8px; row-gap: 3px;
  align-items: baseline; padding: 5px 0; font-size: 12.5px; }
.it + .it { border-top: 1px solid var(--border); }
.it .st { color: var(--ink-3); align-self: center; }
.it.req:not(.ok) .st { color: var(--critical); }
.it .lb { color: var(--ink-1); }
.it .opt { margin-left: 6px; font-size: 11px; color: var(--ink-3); }
.it .dt { color: var(--ink-3); font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* how to fix it: a sentence in the body font, its commands as code */
.it .fix { grid-column: 2 / -1; font-size: 12px; line-height: 1.6; color: var(--ink-2); overflow-wrap: anywhere; }
.it .fix code { padding: 1px 5px; border-radius: 4px; background: var(--surface-2); font-size: 11.5px; }
.allok { grid-column: 1 / -1; display: flex; align-items: flex-start; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.allok .ok { color: var(--good); flex: none; margin-top: 2px; }
.settings { grid-column: 1 / -1; font-size: 12px; overflow-wrap: anywhere; }

/* the provider tabs: a segmented control above the one form shown */
.seg { display: flex; gap: 2px; margin-top: 12px; padding: 2px; border-radius: var(--radius-sm); background: var(--surface-2);
  align-self: flex-start; max-width: 100%; }
.seg > button { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px; border: 0;
  border-radius: calc(var(--radius-sm) - 2px); background: none; font: inherit; font-size: 12.5px; color: var(--ink-2);
  cursor: pointer; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.seg > button.on { background: var(--surface); color: var(--ink-1); font-weight: 600;
  box-shadow: 0 0 0 1px var(--border); }
.dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
.dot.good { background: var(--good); }
.dot.warn { background: var(--warning); }
.form { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; }
.ph2 { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.ph2 a { font-size: 12px; margin-left: auto; }
.form .field { display: grid; grid-template-columns: 64px 1fr; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-3); }
.form .input { height: 30px; font-size: 12.5px; }
/* the global .select is 36px tall; its arrow moves up with it */
.form .select { height: 30px; font-size: 12.5px; background-position: calc(100% - 15px) 12px, calc(100% - 10px) 12px; }
.hint { font-size: 12px; line-height: 1.6; color: var(--ink-3); }
.acts { display: flex; align-items: center; gap: 8px; padding-top: 6px; }
.tres { font-size: 12px; overflow-wrap: anywhere; }
</style>
