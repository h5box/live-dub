// A subtitle row = one line of events.jsonl joined with its timing.jsonl records (subtitle unit + dub result).
import type { DubResult, EventRec, TimingRec } from "../api/types";

export interface Row {
  key: string; pt0: number; pt1: number; spk: number; zh: string; en: string;
  written?: number; slack?: number; mt?: number; lag?: number;   // subtitle: when written, margin before air
  dub?: DubResult; left?: number; tts?: number; factor?: number; clamped?: boolean; err?: string;  // dub result
  clip?: string; gain?: number;                                  // the dub clip (file name) and its level
}

/** the processor does not dub lines shorter than this (livedub/processor.py) */
export const SHORT_S = 0.8;

export const keyOf = (pt0: number) => pt0.toFixed(2);

function get(map: Map<string, Row>, pt0: number): Row {
  const k = keyOf(pt0);
  let r = map.get(k);
  if (!r) {
    map.set(k, { key: k, pt0, pt1: pt0, spk: 0, zh: "", en: "" });
    r = map.get(k)!;      // the stored (possibly reactive) object
  }
  return r;
}

export function mergeEvents(map: Map<string, Row>, evs: EventRec[]) {
  for (const e of evs) {
    if (typeof e.pt0 !== "number") continue;
    const r = get(map, e.pt0);
    if (typeof e.pt1 === "number") r.pt1 = e.pt1;
    if (typeof e.spk === "number") r.spk = e.spk;
    if (typeof e.zh === "string") r.zh = e.zh;
    if (typeof e.en === "string") r.en = e.en;
    if (typeof e.dub === "string" && e.dub) {
      r.clip = e.dub.split(/[\\/]/).pop();
      r.gain = typeof e.gain === "number" ? e.gain : 1;
    }
  }
}

export function mergeTiming(map: Map<string, Row>, ts: TimingRec[]) {
  for (const t of ts) {
    if (typeof t.pt0 !== "number") continue;
    const r = get(map, t.pt0);
    if (typeof t.pt1 === "number") {           // the subtitle unit
      r.pt1 = t.pt1;
      if (typeof t.spk === "number") r.spk = t.spk;
      r.written = t.written; r.slack = t.slack_s; r.mt = t.mt_s; r.lag = t.lag_s;
    } else if (t.dub) {                        // its dub result
      r.dub = t.dub; r.left = t.left_s; r.tts = t.tts_s; r.factor = t.factor; r.clamped = t.clamped; r.err = t.err;
    }
  }
}

export function buildRows(events: EventRec[], timing: TimingRec[]): Row[] {
  const m = new Map<string, Row>();
  mergeEvents(m, events);
  mergeTiming(m, timing);
  return sortRows(m.values());
}

export function sortRows(rows: Iterable<Row>): Row[] {
  return [...rows].filter((r) => r.zh || r.en).sort((a, b) => a.pt0 - b.pt0);
}

/** what the dub column says for a row */
export type DubView = "ok" | "late" | "skipped" | "failed" | "pending" | "short" | "none";

export function dubView(r: Row, aired: boolean): DubView {
  if (r.dub) return r.dub;
  if (r.pt1 - r.pt0 < SHORT_S) return "short";
  return aired ? "none" : "pending";
}

export const DUB_LABEL: Record<DubView, string> = {
  ok: "已配音", late: "来不及", skipped: "跳过", failed: "配音失败", pending: "配音中", short: "短句不配",
  none: "未配音",
};
export const DUB_HINT: Record<DubView, string> = {
  ok: "配音已混进节目",
  late: "合成完时已经过了播出时间，这句没配音",
  skipped: "轮到它时离播出不到 2 秒，没合成",
  failed: "配音服务出错",
  pending: "在排队或合成中",
  short: "短于 0.8 秒的句子不配音",
  none: "没有配音记录（配音服务没开或这句没送去合成）",
};
