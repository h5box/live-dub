<script setup lang="ts">
// The environment page: what this machine still lacks (the backend's environment check, with the command that fixes
// each gap), the resident services (start / stop, where they run, their logs), the online translation keys, the GPU.
import { computed, onMounted, reactive, ref } from "vue";
import Banner from "../components/Banner.vue";
import GpuMeter from "../components/GpuMeter.vue";
import Icon from "../components/Icon.vue";
import SvcState from "../components/SvcState.vue";
import { api, BASE } from "../api/client";
import type { DoctorItem, Provider, SvcName } from "../api/types";
import { ICON, ORDER } from "../lib/serviceInfo";
import { active, checkEnv, focusPreset, live, reloadModels, serviceAction, services } from "../stores/live";

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
const shown = (it: DoctorItem) => showAll.value || !it.ok;
const groupOk = (items: DoctorItem[]) => items.every((i) => i.ok || i.level !== "required");
// with the satisfied ones hidden, complete groups fold into one line so what is missing stands out
const complete = computed(() => groups.value.filter((g) => g.items.every((i) => i.ok)));
const listed = computed(() => (showAll.value ? groups.value : groups.value.filter((g) => !complete.value.includes(g))));

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
    test[p.name] = r.ok ? `能用：「${r.reply}」（${r.wall_s} 秒）` : `不行：${r.error}`;
  } catch (err) { test[p.name] = (err as Error).message; } finally { saving.value = ""; }
}

onMounted(() => { checkEnv(); loadProviders(); });
</script>

<template>
  <div class="view">
    <header class="ph">
      <h1>环境</h1>
      <p>这台机器还缺什么、各个服务的状态和日志、线上翻译的 key。开始翻译时会自动拉起识别和本地翻译
        （打开「输出到 OBS」时还有 MediaMTX）；配音启动要 1.5–3 分钟，要配音就提前开。正在翻译时不能停服务。</p>
    </header>
    <Banner v-if="live.error" tone="critical" dismissible @dismiss="live.error = ''">{{ live.error }}</Banner>

    <section class="card env">
      <header class="card-h">
        <span class="si"><Icon name="check" :size="16" /></span><h2>环境检查</h2>
        <span v-if="live.doctor" class="chip" :class="live.doctor.ok ? 'good' : 'critical'">
          <Icon :name="live.doctor.ok ? 'check' : 'alert'" :size="12" />
          {{ live.doctor.ok ? "必需的都齐了" : `还缺 ${missingReq.length} 项必需的` }}</span>
        <span class="sp" />
        <label class="switch sm"><input v-model="showAll" type="checkbox" />显示已满足的</label>
        <button class="btn sm" :disabled="live.checking" @click="checkEnv">
          <Icon name="restart" :size="13" :class="{ spin: live.checking }" />重新检查</button>
      </header>
      <div class="card-b">
        <p v-if="!live.doctor" class="muted">{{ live.checking ? "检查中…" : "连上后端后自动检查" }}</p>
        <div v-for="g in listed" :key="g.name" class="grp">
          <h3><Icon :name="groupOk(g.items) ? 'check' : 'alert'" :size="13" :class="groupOk(g.items) ? 'ok' : 'bad'" />{{ g.name }}</h3>
          <template v-for="it in g.items" :key="it.label">
            <div v-if="shown(it)" class="it" :class="{ ok: it.ok, req: it.level === 'required' }">
              <Icon :name="it.ok ? 'check' : it.level === 'required' ? 'error' : 'info'" :size="14" class="st" />
              <span class="lb">{{ it.label }}<span v-if="!it.ok && it.level !== 'required'" class="opt">可选</span></span>
              <span class="dt" :title="it.detail">{{ it.detail }}</span>
              <code v-if="it.fix" class="fix">{{ it.fix }}</code>
            </div>
          </template>
        </div>
        <p v-if="!showAll && complete.length" class="allok">
          <Icon name="check" :size="13" class="ok" />{{ complete.map((g) => g.name).join("、") }}：都有了</p>
        <p v-if="live.doctor" class="settings muted">设置文件：<code>{{ live.doctor.settings_file }}</code>
          {{ live.doctor.settings_exists ? "" : "（还没有，用的是默认值；要改路径、端口就照 settings.example.json 建一个）" }}</p>
      </div>
    </section>

    <div class="grid">
      <article v-for="n in ORDER" :key="n" class="card svc">
        <header class="card-h">
          <span class="si"><Icon :name="ICON[n]" :size="16" /></span>
          <h2>{{ svc(n)?.label ?? n }}</h2>
          <SvcState :svc="svc(n)" />
          <span class="sp" />
          <template v-if="svc(n)?.up">
            <button class="btn sm" :disabled="!!live.busy || active || settling(n)" :title="active ? '正在翻译，先停止' : ''"
                    @click="serviceAction(n, 'restart')"><Icon name="restart" :size="13" />重启</button>
            <button class="btn sm" :disabled="!!live.busy || active || settling(n)" :title="active ? '正在翻译，先停止' : ''"
                    @click="serviceAction(n, 'stop')"><Icon name="power" :size="13" />停止</button>
          </template>
          <button v-else-if="!(n === 'mt' && online)" class="btn sm" :disabled="!!live.busy || settling(n) || !live.link"
                  @click="serviceAction(n, 'start')">
            <Icon :name="busy(n) ? 'spinner' : 'play'" :size="12" :class="{ spin: busy(n) }" />启动</button>
        </header>
        <div class="card-b">
          <p class="what">{{ info(n)?.what }}</p>
          <p v-if="n === 'mt' && online" class="note"><Icon name="link" :size="13" />
            {{ active ? "本场" : "当前选的预设" }}用线上翻译 {{ focusPreset?.mt_model }}，不需要这个服务。</p>
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
            <dd class="logs"><code v-for="l in info(n)?.logs ?? []" :key="l">{{ l }}</code>
              <button class="btn ghost sm" @click="toggleLog(n)"><Icon name="log" :size="13" />{{ logs[n] ? "收起" : "看最后几行" }}</button></dd>
          </dl>
          <div v-if="n === 'asr' && svc(n)?.boost?.length" class="boost">
            <span class="k">热词</span>
            <span v-for="b in svc(n)?.boost" :key="b" class="tag">{{ b }}</span>
          </div>
          <Banner v-if="svc(n)?.error" tone="critical" class="err">{{ svc(n)?.error }}</Banner>
          <div v-if="logs[n]" class="logbox">
            <template v-for="(text, f) in logs[n]" :key="f">
              <div class="lf">{{ f }}</div>
              <pre>{{ text || "（还没有内容）" }}</pre>
            </template>
          </div>
        </div>
      </article>

      <article class="card svc">
        <header class="card-h"><span class="si"><Icon name="key" :size="16" /></span><h2>线上翻译</h2></header>
        <div class="card-b">
          <p class="what">没有 N 卡、或者想省显存时用：识别仍在本机，翻译交给线上接口（DeepSeek，或任何 OpenAI 兼容的接口）。
            key 只存在本机的 <code>.secret/</code> 里。</p>
          <div v-for="p in providers" :key="p.name" class="prov">
            <div class="ph2">
              <b>{{ p.label }}</b>
              <span class="chip" :class="p.problem ? 'warning' : 'good'">
                <Icon :name="p.problem ? 'alert' : 'check'" :size="12" />{{ p.problem ? "未配置" : "已配置" }}</span>
              <a v-if="p.key_url" :href="p.key_url" target="_blank" rel="noreferrer" class="muted">申请 key</a>
            </div>
            <label class="field"><span>接口地址</span>
              <input v-model="edit[p.name].base_url" class="input" spellcheck="false" placeholder="https://…/v1" /></label>
            <label class="field"><span>模型</span>
              <input v-model="edit[p.name].model" class="input" spellcheck="false"
                     :placeholder="p.name === 'deepseek' ? '留空用 deepseek-flash' : '比如 qwen-plus、gpt-4.1-mini'" /></label>
            <label class="field"><span>API key</span>
              <input v-model="edit[p.name].api_key" class="input" type="password" autocomplete="off"
                     :placeholder="p.has_key ? '已保存（留空不改）' : '粘贴 key'" /></label>
            <div class="acts">
              <button class="btn sm primary" :disabled="saving === p.name" @click="saveProvider(p)">保存</button>
              <button class="btn sm" :disabled="saving === p.name || !!p.problem" @click="testProvider(p)">测试</button>
              <span class="muted tres">{{ test[p.name] }}</span>
            </div>
          </div>
        </div>
      </article>

      <article class="card svc">
        <header class="card-h"><span class="si"><Icon name="chip" :size="16" /></span><h2>显卡</h2></header>
        <div class="card-b">
          <p v-if="live.status?.gpu?.name" class="what">{{ live.status.gpu.name }}</p>
          <GpuMeter />
          <p class="what gapt">本地翻译约占 5 GB、配音约 6.5 GB。别的程序也在用这张卡时，可能放不下或互相拖慢。</p>
          <Banner v-for="w in live.status?.warnings ?? []" :key="w" tone="warning" class="err">{{ w }}</Banner>
        </div>
      </article>

      <article class="card svc">
        <header class="card-h">
          <span class="si"><Icon name="plug" :size="16" /></span><h2>后端</h2>
          <span class="state" :class="{ off: !live.link }">
            <Icon :name="live.link ? 'check' : 'error'" :size="13" />{{ live.link ? "已连接" : "未连接" }}</span>
        </header>
        <div class="card-b">
          <p class="what">窗口只是界面；起停服务、开始停止都由后端做。关掉窗口，后端和正在进行的翻译照旧。</p>
          <dl>
            <dt>地址</dt><dd><code>{{ BASE || "http://127.0.0.1:8090" }}</code></dd>
            <dt>手动启动</dt><dd><code>uv run python -m livedub.server</code></dd>
            <dt>命令行检查</dt><dd><code>uv run python -m livedub.doctor</code></dd>
          </dl>
        </div>
      </article>
    </div>
  </div>
</template>

<style scoped>
.view { height: 100%; overflow-y: auto; padding: 20px 24px 28px; display: flex; flex-direction: column; gap: 14px; }
.ph h1 { font-size: 20px; font-weight: 600; }
.ph p { margin-top: 4px; color: var(--ink-2); max-width: 820px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(380px, 1fr)); gap: var(--gap); align-items: start; }
.svc .card-h, .env .card-h { gap: 10px; }
.sp { flex: 1; }
.si { width: 28px; height: 28px; border-radius: 7px; display: grid; place-items: center; background: var(--surface-2);
  color: var(--ink-2); }
.what { color: var(--ink-2); }
.gapt { margin-top: 12px; }
.note { display: flex; align-items: center; gap: 6px; margin-top: 8px; padding: 6px 10px; border-radius: var(--radius-sm);
  background: var(--accent-soft); font-size: 12.5px; }
.note .ic { color: var(--accent); }
dl { display: grid; grid-template-columns: 72px 1fr; gap: 6px 10px; margin: 12px 0 0; font-size: 12.5px; }
dt { color: var(--ink-3); }
dd { margin: 0; color: var(--ink-1); min-width: 0; overflow-wrap: anywhere; }
.logs { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
.logs .btn { height: 22px; }
.impl { font-size: 12px; color: var(--ink-2); }
.boost { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; }
.boost .k { font-size: 12.5px; color: var(--ink-3); width: 72px; }
.tag { padding: 1px 8px; border-radius: 4px; background: var(--surface-2); font-size: 12px; color: var(--ink-2); }
.err { margin-top: 12px; }
.logbox { margin-top: 10px; }
.lf { font-size: 11.5px; color: var(--ink-3); margin: 6px 0 2px; }
.logbox pre { max-height: 260px; overflow: auto; margin: 0; padding: 8px 10px; border-radius: var(--radius-sm);
  background: var(--surface-2); font-size: 11.5px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
.state { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink-2); }
.state .ic { color: var(--good); }
.state.off .ic { color: var(--critical); }

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
.it .fix { grid-column: 2 / -1; font-size: 11.5px; color: var(--ink-2); background: var(--surface-2); padding: 3px 7px;
  border-radius: 4px; white-space: normal; overflow-wrap: anywhere; }
.allok { grid-column: 1 / -1; display: flex; align-items: flex-start; gap: 6px; font-size: 12.5px; color: var(--ink-2); }
.allok .ok { color: var(--good); flex: none; margin-top: 2px; }
.settings { grid-column: 1 / -1; font-size: 12px; overflow-wrap: anywhere; }

.prov { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 6px; }
.ph2 { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.ph2 a { font-size: 12px; margin-left: auto; }
.prov .field { display: grid; grid-template-columns: 64px 1fr; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink-3); }
.prov .input { height: 30px; font-size: 12.5px; }
.acts { display: flex; align-items: center; gap: 8px; }
.tres { font-size: 12px; overflow-wrap: anywhere; }
</style>
