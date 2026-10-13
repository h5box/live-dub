<script setup lang="ts">
// The watch view: what to translate (SourceBar), the picture with subtitles and dubs (Player), a row of numbers;
// on the right the subtitle list (click a line to play from it), the margin chart, and services / session log.
import { computed, onMounted, onUnmounted, ref } from "vue";
import Banner from "../components/Banner.vue";
import GpuMeter from "../components/GpuMeter.vue";
import Icon from "../components/Icon.vue";
import Kpi from "../components/Kpi.vue";
import ObsCard from "../components/ObsCard.vue";
import Player from "../components/Player.vue";
import ServiceList from "../components/ServiceList.vue";
import SessionLog from "../components/SessionLog.vue";
import SlackChart from "../components/SlackChart.vue";
import SourceBar from "../components/SourceBar.vue";
import SubtitleTimeline from "../components/SubtitleTimeline.vue";
import { clock, dayLabel, hm, sec1, span } from "../lib/format";
import { load, save } from "../lib/prefs";
import { LOW_MARGIN_S } from "../lib/stats";
import { active, airBase, form, live, p0, player, playhead, programElapsed, rows, session, stats, startSession } from "../stores/live";

defineEmits<{ navigate: [view: "history" | "services"] }>();

const tl = ref<InstanceType<typeof SubtitleTimeline> | null>(null);
const s = computed(() => session.value);
const dubOn = computed(() => s.value?.dub ?? true);
const running = computed(() => s.value?.state === "running");
/** the player waits for the picture (and later follows the schedule) from the moment the session starts */
const underway = computed(() => ["starting", "running"].includes(s.value?.state ?? ""));
const anchor = computed(() => live.status?.health?.anchor ?? null);
/** paced in real time, so the player follows the schedule: a live stream (a link counts as live until ingest
 *  has looked), or any source with OBS output; otherwise it is processed ahead */
const realtime = computed(() => !!s.value?.obs || (s.value?.is_live ?? s.value?.kind !== "file"));
const playout = computed(() => live.status?.health?.playout ?? {});
const tab = ref<"subs" | "state">(load<string>("liveTab", "subs") === "state" ? "state" : "subs");
const diagnostics = ref(false);
function setTab(t: typeof tab.value) { tab.value = t; save("liveTab", t); }

/** seconds of program the session has produced on the default schedule; when over, from first frame to the end */
const aired = computed(() => {
  if (running.value) return programElapsed.value;
  if (!s.value?.ended || !s.value.started) return null;
  return s.value.ended - (anchor.value ? anchor.value.T0 + anchor.value.delay : s.value.started + s.value.delay_s);
});

const kpi = computed(() => {
  const st = stats.value, d = st.dub, p = playout.value;
  const problems = [d.late && `来不及 ${d.late}`, d.skipped && `跳过 ${d.skipped}`, d.failed && `失败 ${d.failed}`]
    .filter(Boolean).join(" · ");
  return {
    time: { label: aired.value !== null && aired.value < 0 ? "开始播放还有" : running.value ? "已进行" : "时长",
            value: aired.value === null ? "—" : aired.value < 0 ? `${Math.ceil(-aired.value)} 秒` : clock(aired.value),
            sub: s.value ? `延时 ${s.value.delay_s} 秒` : "还没开始过" },
    subs: { value: String(st.subs), sub: st.mtMed !== null ? `翻译中位 ${sec1(st.mtMed)} 秒` : "" },
    dub: !dubOn.value ? { value: "关", unit: "", sub: "只出字幕", tone: undefined, toneLabel: "" } : {
      value: String(d.ok), unit: "句",
      sub: problems || (d.short ? `短句不配 ${d.short}` : d.pending ? `排队 ${d.pending}` : "没有漏配"),
      tone: d.failed ? "critical" as const : d.late + d.skipped ? "warning" as const : undefined,
      toneLabel: d.failed ? "有失败" : "有漏配",
    },
    margin: {
      value: sec1(st.slackMed), unit: st.slackMed !== null ? "秒" : "",
      sub: st.slackMin !== null ? `中位数 · 最少 ${sec1(st.slackMin)} 秒` : "",
      tone: st.slackMin === null ? undefined : st.slackMin < 0 ? "critical" as const : st.slackMin < LOW_MARGIN_S ? "warning" as const : undefined,
      toneLabel: st.slackMin !== null && st.slackMin < 0 ? "迟到" : "偏低",
    },
    obs: { value: p.slate === undefined ? "—" : String(p.slate), sub: p.max_late_ms !== undefined ? `迟到 ${p.max_late_ms} ms` : "",
           tone: p.slate ? "warning" as const : undefined },
  };
});

const ended = computed(() => !active.value && !!s.value && ["ended", "failed"].includes(s.value.state));
const envMissing = computed(() => live.doctor?.missing_required ?? []);
const media = computed(() => (s.value && s.value.state !== "failed" ? s.value.media ?? null : null));
const nowProgram = computed(() => (playhead.value !== null && p0.value !== undefined ? playhead.value - p0.value : null));
function retry(subtitles: boolean) {
  const old = s.value;
  if (!old) return;
  startSession({ preset: old.preset, src: old.src ?? form.src, delay: old.delay_s,
    dub: subtitles ? false : old.dub, obs: old.obs ?? false, mt_model: old.mt_model ?? form.mt_model });
}

// drag a video file onto the window (Tauri gives the real path)
const dragging = ref(false);
let unlisten: (() => void) | null = null;
onMounted(async () => {
  if (!("__TAURI_INTERNALS__" in window)) return;
  const { getCurrentWebview } = await import("@tauri-apps/api/webview");
  unlisten = await getCurrentWebview().onDragDropEvent((e) => {
    const p = e.payload;
    if (p.type === "enter" || p.type === "over") dragging.value = !active.value;
    else if (p.type === "leave") dragging.value = false;
    else if (p.type === "drop") {
      dragging.value = false;
      if (!active.value && p.paths.length) form.src = p.paths[0];
    }
  });
});
onUnmounted(() => unlisten?.());
</script>

<template>
  <div class="view">
    <SourceBar />

    <div v-if="!live.link || envMissing.length || live.status?.warnings?.length || live.error" class="banners">
      <Banner v-if="!live.link" tone="critical">
        正在连接本机服务。长时间无法连接时，请重新打开程序或检查「环境」页。
      </Banner>
      <Banner v-else-if="envMissing.length && !active" tone="warning">
        这台机器还缺：{{ envMissing.join("、") }}。
        <button class="btn sm" @click="$emit('navigate', 'services')">去「环境」页看怎么装</button>
      </Banner>
      <Banner v-for="w in live.status?.warnings ?? []" :key="w" tone="warning">{{ w }}</Banner>
      <Banner v-if="live.error" tone="critical" dismissible @dismiss="live.error = ''">{{ live.error }}</Banner>
    </div>

    <div class="body">
      <div class="main">
        <Player :media="media" :session="s?.name ?? null" :rows="rows" :anchor="anchor" :running="underway" :dub-on="dubOn"
                :is-live="realtime" :processed="live.status?.health?.progress?.ranges">
          <template #idle>
            <div class="idlehint">
              <template v-if="s?.state === 'starting'">
                <Icon name="spinner" :size="22" class="spin" /><p>{{ s.log.at(-1)?.[1] ?? "准备中" }}</p>
              </template>
              <template v-else-if="s?.state === 'failed'">
                <Icon name="error" :size="22" class="bad" /><p>上一场没开起来：{{ s.log.at(-1)?.[1] }}</p>
                <button class="btn" @click="retry(false)">重试</button><button class="btn" @click="retry(true)">只用字幕重试</button>
              </template>
              <template v-else>
                <h3>贴一个直播或视频链接，或者选一个本地视频</h3>
                <p>直播延时 {{ form.delay }} 秒；视频提前准备字幕，可暂停和跳转。</p>
                <p class="muted">当前支持英语转简体中文；字幕和配音按句子时间与画面对齐。</p>
              </template>
            </div>
          </template>
        </Player>

        <button class="btn sm" @click="diagnostics = !diagnostics">{{ diagnostics ? '收起运行详情' : '运行详情' }}</button>
        <div v-if="diagnostics" class="kpis">
          <Kpi :label="kpi.time.label" :value="kpi.time.value" :sub="kpi.time.sub" live />
          <Kpi label="字幕" :value="kpi.subs.value" unit="句" :sub="kpi.subs.sub" />
          <Kpi label="配音" :value="kpi.dub.value" :unit="kpi.dub.unit" :sub="kpi.dub.sub" :tone="kpi.dub.tone" :tone-label="kpi.dub.toneLabel" />
          <Kpi label="字幕余量" :value="kpi.margin.value" :unit="kpi.margin.unit" :sub="kpi.margin.sub" :tone="kpi.margin.tone" :tone-label="kpi.margin.toneLabel" />
          <Kpi v-if="s?.obs" label="OBS 断流帧" :value="kpi.obs.value" :sub="kpi.obs.sub" :tone="kpi.obs.tone" tone-label="有断流" />
        </div>

        <section v-if="diagnostics && rows.length" class="card chartcard">
          <header class="card-h"><h2>余量</h2><span class="muted">{{ running ? "最近 10 分钟" : "整场" }} · 做好时离默认播放时刻还有几秒，低于 5 秒可能来不及</span></header>
          <div class="card-b">
            <SlackChart :rows="rows" :p0="p0" :span="running ? 600 : 0" :now="nowProgram" :height="130"
                        :delay="s?.delay_s ?? 30" :dub-on="dubOn" @pick="setTab('subs'); tl?.reveal($event)" />
          </div>
        </section>
      </div>

      <aside class="side card">
        <div class="tabs" role="tablist">
          <button role="tab" :aria-selected="tab === 'subs'" :class="{ on: tab === 'subs' }" @click="setTab('subs')">字幕 <span v-if="rows.length" class="num">{{ rows.length }}</span></button>
          <button role="tab" :aria-selected="tab === 'state'" :class="{ on: tab === 'state' }" @click="setTab('state')">状态</button>
          <span class="sp" />
          <span v-if="ended && s" class="chip" :class="{ critical: s.state === 'failed' }" :title="s.title">
            {{ s.state === "failed" ? "上一场没开起来" : "上一场" }} · {{ dayLabel(s.created) }} {{ hm(s.created) }}
            <template v-if="aired !== null && s.state === 'ended'"> · {{ span(aired) }}</template>
          </span>
        </div>

        <SubtitleTimeline v-show="tab === 'subs'" ref="tl" :rows="rows" :playhead="media ? playhead : null" :p0="p0"
                          :air-base="airBase" :dub-on="dubOn" :window="400" start-at="bottom" :seekable="!!media && !!player.seek"
                          @seek="player.seek?.($event)">
          <template #empty>
            <p class="none">{{ s?.state === "running" ? "已开始，识别出第一句后会出现在这里" : "开始后，字幕会一句句出现在这里，点一句就从那句开始播" }}</p>
          </template>
        </SubtitleTimeline>

        <div v-show="tab === 'state'" class="pane">
          <h4>服务</h4>
          <ServiceList @manage="$emit('navigate', 'services')" />
          <hr class="sep gap" />
          <GpuMeter />
          <template v-if="s?.obs">
            <hr class="sep gap" /><h4>OBS 输出</h4><ObsCard />
          </template>
          <template v-if="s">
            <hr class="sep gap" /><h4>{{ active ? "这一场的记录" : "上一场的记录" }}</h4>
            <SessionLog :log="s.log" :busy="s.state === 'starting'" />
          </template>
        </div>
      </aside>
    </div>

    <div v-if="dragging" class="drop"><Icon name="film" :size="28" /><p>松开，用这个视频</p></div>
  </div>
</template>

<style scoped>
.view { position: relative; height: 100%; display: flex; flex-direction: column; gap: var(--gap); padding: var(--gap); min-width: 0; }
.banners { display: flex; flex-direction: column; gap: 8px; }
.body { flex: 1; min-height: 0; display: flex; gap: var(--gap); }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: var(--gap); overflow-y: auto; }
.main > * { flex-shrink: 0; }     /* the picture keeps 16:9; the column scrolls instead */
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(118px, 1fr)); gap: 8px; }
.side { width: 400px; flex-shrink: 0; display: flex; flex-direction: column; overflow: hidden; }
.tabs { display: flex; align-items: center; gap: 2px; padding: 0 10px; border-bottom: 1px solid var(--border); min-height: 42px; }
.tabs > button { height: 42px; padding: 0 10px; border: 0; border-bottom: 2px solid transparent; background: none;
  font: inherit; color: var(--ink-2); cursor: pointer; margin-bottom: -1px; }
.tabs > button.on { color: var(--ink-1); font-weight: 600; border-bottom-color: var(--accent); }
.tabs > button span { color: var(--ink-3); font-weight: 400; margin-left: 2px; }
.tabs .sp { flex: 1; }
.tabs .chip { max-width: 190px; overflow: hidden; text-overflow: ellipsis; }
.pane { flex: 1; overflow-y: auto; padding: 14px; }
.pane h4 { font-size: 12.5px; font-weight: 600; color: var(--ink-2); margin: 0 0 6px; }
.chartcard { flex-shrink: 0; }
.chartcard .card-h .muted { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gap { margin: 12px 0; }
.none { padding: 40px 24px; text-align: center; color: var(--ink-3); font-size: 13px; }

.idlehint { max-width: 520px; display: flex; flex-direction: column; align-items: center; gap: 10px; color: rgba(255, 255, 255, 0.85); }
.idlehint h3 { font-size: 17px; font-weight: 600; color: #fff; }
.idlehint p { font-size: 13px; line-height: 1.6; }
.idlehint .muted { color: rgba(255, 255, 255, 0.5); font-size: 12px; }
.idlehint .bad { color: var(--critical); }

.drop { position: absolute; inset: 8px; z-index: 20; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; border: 2px dashed var(--accent); border-radius: var(--radius); background: color-mix(in srgb, var(--plane) 88%, transparent);
  color: var(--ink-1); font-size: 15px; pointer-events: none; }
.drop .ic { color: var(--accent); }

@media (max-width: 1150px) { .side { width: 340px; } }
@media (min-width: 1600px) { .side { width: 460px; } }
</style>
