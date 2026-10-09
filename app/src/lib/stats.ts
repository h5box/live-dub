import { median } from "./format";
import { dubView, type Row } from "./rows";

export interface Stats {
  subs: number;
  dub: { ok: number; late: number; skipped: number; failed: number; short: number; pending: number; none: number };
  slackMed: number | null; slackMin: number | null;   // subtitle margin before air (s)
  leftMed: number | null; leftMin: number | null;     // dub margin before air (s)
  mtMed: number | null;                               // translation time per line (s)
}

/** aired(r): whether the row has already gone on air (decides pending vs none for rows without a dub result) */
export function computeStats(rows: Row[], aired: (r: Row) => boolean = () => true): Stats {
  const dub = { ok: 0, late: 0, skipped: 0, failed: 0, short: 0, pending: 0, none: 0 };
  const slack: number[] = [], left: number[] = [], mt: number[] = [];
  for (const r of rows) {
    dub[dubView(r, aired(r))]++;
    if (r.slack !== undefined) slack.push(r.slack);
    if (r.dub === "ok" && r.left !== undefined) left.push(r.left);
    if (r.mt !== undefined) mt.push(r.mt);
  }
  return {
    subs: rows.length, dub,
    slackMed: median(slack), slackMin: slack.length ? Math.min(...slack) : null,
    leftMed: median(left), leftMin: left.length ? Math.min(...left) : null,
    mtMed: median(mt),
  };
}

/** margin below this (s) is shown as a warning */
export const LOW_MARGIN_S = 5;
