// The live state: one SSE connection for the whole window (status every 2 s, the current session's records),
// a 4 Hz clock for the playhead, and the actions. Module-level reactive state; components import what they use.
import { computed, reactive, watch } from "vue";
import { api } from "../api/client";
import { subscribe } from "../api/stream";
import type { DoctorReport, Downloadable, MtModel, Preset, StartOpts, Status, SvcInfo, SvcName } from "../api/types";
import { mergeEvents, mergeTiming, sortRows, type Row } from "../lib/rows";
import { load, save } from "../lib/prefs";
import { computeStats } from "../lib/stats";
import { version as APP_VERSION } from "../../package.json";

export { APP_VERSION };

export const live = reactive({
  link: false,                    // SSE connected
  linkedOnce: false,
  status: null as Status | null,
  skew: 0,                        // backend clock minus this window's clock (s); 0 on the same machine
  now: Date.now() / 1000,         // backend clock, ticking
  presets: [] as Preset[],
  models: [] as MtModel[],
  svcInfo: null as Record<SvcName, SvcInfo> | null,   // what each service is and where it runs here
  doctor: null as DoctorReport | null,                // the environment check
  checking: false,
  downloadables: {} as Record<string, Downloadable>,  // what this machine can download (GET /api/downloads)
  name: null as string | null,    // the session the rows belong to
  busy: "",                       // the action in flight: start | stop | asr:start ...
  error: "",                      // the last failed action, until dismissed or the next action
});

export const rowMap = reactive(new Map<string, Row>());

/** the start form (remembered per window) */
export const form = reactive(load("form2", { preset: "default", src: "", delay: 30, dub: false, obs: false, mt_model: "" }));
watch(form, () => save("form2", { ...form }), { deep: true });

/** the in-app player: the program time it shows (null = nothing loaded), whether it follows the default delay, and
 *  its seek function (set by the player while mounted) */
export const player = reactive({
  pt: null as number | null,
  follow: true,
  seek: null as ((pt: number) => void) | null,
});

// ---- derived --------------------------------------------------------------------------------------------
export const session = computed(() => live.status?.session ?? null);
export const services = computed(() => live.status?.services ?? null);
export const downloads = computed(() => live.status?.downloads ?? {});
/** the backend on the port is not this window's release (one left running from before an update): its version,
 *  "" when it is too old to say; null when they match or nothing is connected */
export const staleBackend = computed<string | null>(() =>
  live.status && live.status.version !== APP_VERSION ? live.status.version ?? "" : null);
export const active = computed(() => ["starting", "running", "stopping"].includes(session.value?.state ?? ""));
export const onAir = computed(() => session.value?.state === "running");
export const rows = computed(() => sortRows(rowMap.values()));
export const sessionPreset = computed(() => live.presets.find((p) => p.name === session.value?.preset));
export const formPreset = computed(() => live.presets.find((p) => p.name === form.preset));
/** the translation model the next session uses: the one picked (on the watch page or the environment page), else the
 *  preset's, else settings mt.default */
export const nextModel = computed(() =>
  form.mt_model || formPreset.value?.mt_model || live.models.find((m) => m.default)?.key || "");
/** the translation model that matters now: the running session's, else the next one's */
export const focusModel = computed(() => {
  const key = active.value ? session.value?.mt_model || sessionPreset.value?.mt_model : nextModel.value;
  return live.models.find((m) => m.key === key) ?? null;
});

/** wall time at which PT 0 would air: air(pt) = base + pt. From the playout's anchor, else from any written row
 *  (written + slack is that row's air time). Undefined until the playout has its first frame. */
export const airBase = computed<number | undefined>(() => {
  const a = live.status?.health?.anchor;
  if (a) return a.T0 + a.delay - a.p0;
  for (let i = rows.value.length - 1; i >= 0; i--) {
    const r = rows.value[i];
    if (r.written !== undefined && r.slack !== undefined) return r.written + r.slack - r.pt0;
  }
  return undefined;
});

/** program start (PT of the first frame): from the anchor, else the first row */
export const p0 = computed<number | undefined>(() => live.status?.health?.anchor?.p0 ?? rows.value[0]?.pt0);

/** PT that the default schedule shows now (first frame + delay), while the session is running */
export const schedHead = computed<number | null>(() =>
  onAir.value && airBase.value !== undefined ? live.now - airBase.value : null);

/** PT on screen: the player's position when it has one, else the default schedule */
export const playhead = computed<number | null>(() => player.pt ?? schedHead.value);

/** seconds of program on the default schedule so far (negative: the first frame is still inside the delay) */
export const programElapsed = computed<number | null>(() =>
  schedHead.value !== null && p0.value !== undefined ? schedHead.value - p0.value : null);

export const stats = computed(() => {
  const ph = playhead.value;
  return computeStats(rows.value, (r) => ph === null || r.pt1 <= ph);
});

// ---- stream ---------------------------------------------------------------------------------------------
let stop: (() => void) | null = null;
let timer = 0;

export function connect() {
  if (stop) return;
  stop = subscribe({
    status(s) {
      live.status = s;
      if (typeof s.t === "number") {      // only a gross clock difference counts (another machine); same machine: 0
        const d = s.t - Date.now() / 1000;
        live.skew = Math.abs(d) > 30 ? d : 0;
      }
    },
    reset(name) { rowMap.clear(); live.name = name; },
    event(evs) { mergeEvents(rowMap, evs); },
    timing(ts) { mergeTiming(rowMap, ts); },
    link(ok) {
      live.link = ok;
      if (ok) {
        live.linkedOnce = true;
        if (!live.presets.length) loadPresets();
        if (!live.doctor) checkEnv();
        api.downloadables().then((d) => { live.downloadables = d; }, () => {});
      }
    },
  });
  timer = window.setInterval(() => { live.now = Date.now() / 1000 + live.skew; }, 250);
}

export function disconnect() {
  stop?.(); stop = null;
  clearInterval(timer);
}

// ---- actions ----------------------------------------------------------------------------------------------
export async function loadPresets() {
  try {
    [live.presets, live.models] = await Promise.all([api.configs(), api.models()]);
  } catch { return; /* the stream retries; presets load on reconnect */ }
  // keep the remembered pick if it still exists, else the generic preset
  const ps = live.presets;
  if (ps.length && !ps.some((p) => p.name === form.preset)) {
    const p = ps.find((x) => x.name === "default") ?? ps[0];
    form.preset = p.name;
    form.delay = p.delay_s;
  }
  const selected = form.mt_model || live.models.find((m) => m.default)?.key;
  if (!live.models.find((m) => m.key === selected)?.ready) {
    form.mt_model = live.models.find((m) => m.ready)?.key ?? "";
  }
}

/** the environment check and the services' descriptions (both depend on this machine's settings) */
export async function checkEnv() {
  if (live.checking) return;
  live.checking = true;
  try {
    [live.doctor, live.svcInfo] = await Promise.all([api.doctor(), api.servicesInfo()]);
  } catch { /* shown as "not connected" anyway */ } finally {
    live.checking = false;
  }
}

/** after a change that affects which models can run (a key saved, a model downloaded) */
export async function reloadModels() {
  try { live.models = await api.models(); } catch { /* keep the old list */ }
}

// ---- downloads ------------------------------------------------------------------------------------------
/** queue a download (resume a paused one), or switch a running one to `source` */
export async function download(key: string, source = "") {
  try {
    const d = await api.download(key, source);
    if (live.status) live.status.downloads = d;
  } catch (e) { live.error = (e as Error).message; }
}
export async function pauseDownload(key: string) {
  try {
    const d = await api.pauseDownload(key);
    if (live.status) live.status.downloads = d;
  } catch (e) { live.error = (e as Error).message; }
}
// a finished download changes what is ready: check again
const doneSeen = new Set<string>();
watch(downloads, (d) => {
  const fresh = Object.entries(d).filter(([k, j]) => j.state === "done" && !doneSeen.has(k));
  for (const [k] of fresh) doneSeen.add(k);
  for (const [k, j] of Object.entries(d)) if (j.state !== "done") doneSeen.delete(k);
  if (fresh.length) { checkEnv(); reloadModels(); }
});

async function run(label: string, f: () => Promise<unknown>) {
  live.busy = label;
  live.error = "";
  try {
    await f();
    live.status = await api.status();
  } catch (e) {
    live.error = (e as Error).message;
  } finally {
    live.busy = "";
  }
}

export const startSession = (o: StartOpts) => run("start", () => api.start(o));
export const stopSession = () => run("stop", () => api.stop());
export const serviceAction = (name: SvcName, action: "start" | "stop" | "restart") =>
  run(`${name}:${action}`, () => api.service(name, action, name === "mt" ? { model: localModel() } : {}));
/** what (re)starting the translation service loads: the next session's model when it is a local one, else the one
 *  running (the backend falls back to settings mt.default) */
function localModel(): string | undefined {
  const m = live.models.find((x) => x.key === nextModel.value);
  return m && !m.online ? m.key : services.value?.mt?.model ?? undefined;
}
