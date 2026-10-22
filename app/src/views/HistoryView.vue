<script setup lang="ts">
import { computed, onActivated, onMounted, ref, watch } from "vue";
import Banner from "../components/Banner.vue";
import Icon from "../components/Icon.vue";
import Kpi from "../components/Kpi.vue";
import SessionLog from "../components/SessionLog.vue";
import SlackChart from "../components/SlackChart.vue";
import SubtitleTimeline from "../components/SubtitleTimeline.vue";
import Player from "../components/Player.vue";
import { BASE } from "../api/client";
import { load } from "../lib/prefs";
import { dayLabel, hm, sec1, span } from "../lib/format";
import { buildRows } from "../lib/rows";
import { computeStats, LOW_MARGIN_S } from "../lib/stats";
import { history, loadList, select, remove } from "../stores/history";
import { live } from "../stores/live";

const emit = defineEmits<{ navigate: [view: "live"] }>();

onMounted(loadList);
onActivated(loadList);
// a session that just ended shows up without a manual refresh
watch(() => live.status?.session?.state, (st, old) => { if (st !== old) loadList(); });

const groups = computed(() => {
  const out: { day: string; items: typeof history.list }[] = [];
  for (const s of history.list) {
    const day = dayLabel(s.created);
    if (out.at(-1)?.day !== day) out.push({ day, items: [] });
    out.at(-1)!.items.push(s);
  }
  return out;
});
const lasted = (s: { started?: number | null; ended?: number | null }) =>
  s.started && s.ended ? span(s.ended - s.started) : "";

const d = computed(() => history.detail);
const info = computed(() => d.value?.info);
// the preset's display name, as the 场景 picker shows it; a preset deleted since falls back to its file name
const presetTitle = computed(() => live.presets.find((p) => p.name === info.value?.preset)?.title ?? info.value?.preset);
const rows = computed(() => (d.value ? buildRows(d.value.events, d.value.timing) : []));
const st = computed(() => computeStats(rows.value));
const p0 = computed(() => d.value?.anchor?.p0 ?? rows.value[0]?.pt0);
const airBase = computed(() => (d.value?.anchor ? d.value.anchor.T0 + d.value.anchor.delay - d.value.anchor.p0 : undefined));

const q = ref("");
const exportLanguage = ref("both");
const replay = ref(false);
const confirm = ref<"cache" | "delete" | null>(null);
const removing = ref(false);
watch(() => history.selected, () => { replay.value = false; confirm.value = null; });
const resumable = computed(() => !replay.value && info.value && load<number | null>(`position:${info.value.name}`, null) !== null);
const retained = computed(() => info.value?.media && !["starting", "running", "stopping"].includes(info.value.state));
async function cleanup() {
  if (!info.value || !confirm.value) return;
  removing.value = true;
  try { replay.value = false; await remove(info.value.name, confirm.value === "cache"); confirm.value = null; }
  catch (e) { history.detailError = (e as Error).message; }
  finally { removing.value = false; }
}
const tab = ref<"subs" | "log">("subs");
watch(() => [history.selected, d.value] as const, () => {
  q.value = "";
  tab.value = d.value && !d.value.events.length ? "log" : "subs";
});
const reason = computed(() => (info.value?.state === "failed" ? info.value.log.at(-1)?.[1] ?? "" : ""));
const shown = computed(() => {
  const k = q.value.trim().toLowerCase();
  return k ? rows.value.filter((r) => r.zh.toLowerCase().includes(k) || r.en.toLowerCase().includes(k)) : rows.value;
});
const tl = ref<InstanceType<typeof SubtitleTimeline> | null>(null);
function pick(key: string) { q.value = ""; tab.value = "subs"; setTimeout(() => tl.value?.reveal(key)); }

const STATE: Record<string, string> = { starting: "准备中", running: "翻译中", stopping: "停止中", ended: "已结束", failed: "启动失败" };
</script>

<template>
  <div class="view">
    <aside class="card list">
      <header class="card-h">
        <h2>历史场次</h2><span class="muted num">{{ history.list.length }}</span><span class="sp" />
        <button class="btn ghost sm icon" title="刷新" @click="loadList"><Icon name="restart" :size="14" /></button>
      </header>
      <div class="scroll">
        <p v-if="history.error" class="err">{{ history.error }}</p>
        <p v-else-if="history.loaded && !history.list.length" class="none">暂无场次</p>
        <template v-for="g in groups" :key="g.day">
          <div class="day">{{ g.day }}</div>
          <button v-for="s in g.items" :key="s.name" class="item" :class="{ on: history.selected === s.name }"
                  @click="select(s.name)">
            <span class="time num">{{ hm(s.created) }}</span>
            <span class="name" :title="s.title">{{ s.title }}</span>
            <span class="meta">
              <template v-if="s.state === 'running'"><i class="air" />翻译中</template>
              <template v-else-if="s.state === 'failed'"><Icon name="error" :size="12" class="bad" />启动失败</template>
              <template v-else>{{ lasted(s) || STATE[s.state] }}</template>
              · 延时 {{ s.delay_s }} 秒 · {{ s.dub ? "配音" : "仅字幕" }}
            </span>
          </button>
        </template>
      </div>
    </aside>

    <section class="card detail">
      <div v-if="!info" class="placeholder">
        <Icon v-if="history.loading" name="spinner" :size="20" class="spin" />
        <p v-else-if="history.detailError">{{ history.detailError }}</p>
        <div v-else-if="history.loaded && !history.error && !history.list.length" class="first">
          <p>在「观看」页开始后，每一场的字幕、配音和运行记录会保存在这里，可回放和导出字幕。</p>
          <button class="btn primary" @click="emit('navigate', 'live')">去观看</button>
        </div>
        <p v-else>选择一场查看详情</p>
      </div>
      <template v-else>
        <header class="dh">
          <div class="dt">
            <h1 :title="info.title">{{ info.title }}</h1>
            <p class="meta">
              {{ dayLabel(info.created) }} {{ hm(info.started ?? info.created) }}<template v-if="info.started && info.ended">–{{ hm(info.ended) }} · {{ span(info.ended - info.started) }}</template>
              · 延时 {{ info.delay_s }} 秒 · {{ info.dub ? "中文配音" : "仅字幕" }} · 场景 {{ presetTitle }}
            </p>
          </div>
          <span v-if="info.state === 'failed'" class="chip critical"><Icon name="error" :size="12" />启动失败</span>
          <span v-else-if="info.state === 'running'" class="chip"><i class="air" />翻译中</span>
        </header>
        <div class="history-actions">
          <button v-if="retained" class="btn primary" @click="replay = !replay">{{ replay ? '收起播放器' : resumable ? '继续观看' : '回放视频' }}</button>
          <span v-else class="muted">{{ info.state === 'running' ? '本场仍在进行，请在「观看」页播放' : '视频缓存已清理，字幕仍可阅读和导出' }}</span>
          <select v-model="exportLanguage" class="select" aria-label="导出字幕语言"><option value="both">双语</option><option value="zh">中文</option><option value="en">原文</option></select>
          <a class="btn" :href="`${BASE}/api/sessions/${encodeURIComponent(info.name)}/subtitles.srt?language=${exportLanguage}`" download="subtitles.srt">导出字幕</a>
          <span class="muted">占用 {{ ((info.storage_bytes ?? 0) / 1024 ** 2).toFixed(0) }} MB</span>
          <template v-if="!['starting', 'running', 'stopping'].includes(info.state)">
            <button class="btn" :disabled="removing" @click="confirm = 'cache'">清理缓存</button>
            <button class="btn danger" :disabled="removing" @click="confirm = 'delete'">删除本场</button>
          </template>
        </div>
        <Banner v-if="confirm" tone="warning">{{ confirm === 'cache' ? '将清理本场的视频和配音缓存，字幕和记录会保留。' : '将删除本场的全部缓存、字幕和记录，此操作无法撤销。' }}<button class="btn danger sm" :disabled="removing" @click="cleanup">确认{{ confirm === 'cache' ? '清理' : '删除' }}</button><button class="btn sm" @click="confirm = null">取消</button></Banner>
        <Banner v-if="history.detailError" tone="critical">{{ history.detailError }}</Banner>
        <div v-if="replay && retained" class="replay"><Player :media="info.media ?? null" :session="info.name" :rows="rows" :anchor="d?.anchor ?? null" :running="false" :dub-on="info.dub" :is-live="false" /></div>
        <Banner v-if="history.loading === info.name" tone="info">正在读取…</Banner>
        <Banner v-if="reason" tone="critical">{{ reason }}</Banner>
        <div v-if="rows.length" class="kpis">
          <Kpi label="字幕" :value="String(st.subs)" unit="句" :sub="st.mtMed !== null ? `翻译用时中位数 ${sec1(st.mtMed)} 秒` : ''" />
          <Kpi label="配音" :value="info.dub ? String(st.dub.ok) : '未开启'" :unit="info.dub ? '句' : ''"
               :sub="info.dub ? [st.dub.late && `未赶上 ${st.dub.late}`, st.dub.skipped && `跳过 ${st.dub.skipped}`, st.dub.failed && `失败 ${st.dub.failed}`, st.dub.short && `短句不配音 ${st.dub.short}`].filter(Boolean).join(' · ') || '无遗漏' : '本场仅字幕'"
               :tone="st.dub.failed ? 'critical' : st.dub.late + st.dub.skipped ? 'warning' : undefined"
               :tone-label="st.dub.failed ? '有失败' : '有遗漏'" />
          <Kpi label="字幕余量（中位数）" :value="sec1(st.slackMed)" :unit="st.slackMed !== null ? '秒' : ''"
               :sub="st.slackMin !== null ? `最低 ${sec1(st.slackMin)} 秒` : ''"
               :tone="st.slackMin !== null && st.slackMin < LOW_MARGIN_S ? 'warning' : undefined" tone-label="偏低" />
          <Kpi label="配音余量（中位数）" :value="sec1(st.leftMed)" :unit="st.leftMed !== null ? '秒' : ''"
               :sub="st.leftMin !== null ? `最低 ${sec1(st.leftMin)} 秒` : ''" />
        </div>
        <div v-if="rows.length" class="chartbox">
          <SlackChart :rows="rows" :p0="p0" :height="150" :delay="info.delay_s" :dub-on="info.dub" @pick="pick" />
        </div>
        <div class="tabs">
          <button :class="{ on: tab === 'subs' }" @click="tab = 'subs'">字幕 <span class="num">{{ rows.length }}</span></button>
          <button :class="{ on: tab === 'log' }" @click="tab = 'log'">记录 <span class="num">{{ info.log.length }}</span></button>
          <span class="sp" />
          <label v-if="tab === 'subs'" class="search">
            <Icon name="search" :size="14" />
            <input v-model="q" class="input" placeholder="搜索中文或英文" />
            <span v-if="q" class="muted num">{{ shown.length }} 句</span>
          </label>
        </div>
        <SubtitleTimeline v-if="tab === 'subs'" ref="tl" :rows="shown" :p0="p0" :air-base="airBase" :dub-on="info.dub" :q="q">
          <template #empty><p class="none pad">{{ q ? "没有匹配的句子" : "本场没有字幕" }}</p></template>
        </SubtitleTimeline>
        <div v-else class="logbox"><SessionLog :log="info.log" :max="1000" /></div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.history-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 0 16px 12px; }
.replay { padding: 0 16px 12px; max-height: 55vh; overflow-y: auto; flex-shrink: 0; }
.view { height: 100%; display: flex; gap: var(--gap); padding: var(--gap); min-width: 0; }
.list { width: 288px; flex-shrink: 0; display: flex; flex-direction: column; overflow: hidden; }
.list > .card-h { border-bottom: 1px solid var(--border); }
.scroll { flex: 1; overflow-y: auto; padding: 4px 8px 12px; }
.day { position: sticky; top: -4px; z-index: 1; padding: 10px 6px 4px; font-size: 12px; font-weight: 600; color: var(--ink-3);
  background: var(--surface); }
.item { width: 100%; display: grid; grid-template-columns: 44px minmax(0, 1fr); column-gap: 8px; row-gap: 1px; text-align: left;
  padding: 8px 8px; border: 0; border-radius: 8px; background: transparent; color: var(--ink-1); font: inherit; cursor: pointer; }
.item:hover { background: var(--surface-2); }
.item.on { background: var(--accent-soft); }
.time { grid-row: span 2; color: var(--ink-2); font-size: 12.5px; padding-top: 1px; }
.name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta { display: flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--ink-3); white-space: nowrap; }
.bad { color: var(--critical); }
.air { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--onair); }
.err, .none { padding: 16px 8px; font-size: 12px; color: var(--ink-3); }
.pad { padding: 24px; text-align: center; }

.detail { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
.placeholder { flex: 1; display: grid; place-items: center; color: var(--ink-3); }
.placeholder .first { display: grid; justify-items: center; gap: 14px; max-width: 340px; text-align: center; line-height: 1.6; }
.dh { display: flex; align-items: flex-start; gap: 12px; padding: 14px 16px 10px; }
.dt { flex: 1; min-width: 0; }
.dh h1 { font-size: 17px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dh .meta { margin-top: 3px; font-size: 12px; color: var(--ink-2); white-space: normal; }
.detail > .banner { margin: 0 16px 8px; }
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; padding: 0 16px; }
.chartbox { padding: 12px 16px 8px; }
.tabs { display: flex; align-items: center; gap: 4px; padding: 0 12px; border-bottom: 1px solid var(--border); }
.tabs > button { height: 36px; padding: 0 10px; border: 0; border-bottom: 2px solid transparent; background: none;
  font: inherit; color: var(--ink-2); cursor: pointer; margin-bottom: -1px; }
.tabs > button.on { color: var(--ink-1); font-weight: 600; border-bottom-color: var(--accent); }
.tabs > button span { color: var(--ink-3); font-weight: 400; margin-left: 2px; }
.tabs .sp { flex: 1; }
.search { display: flex; align-items: center; gap: 6px; color: var(--ink-3); }
.search .input { height: 28px; width: 180px; }
.logbox { flex: 1; overflow-y: auto; padding: 14px 16px; }
</style>
