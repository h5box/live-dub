// Number and time formatting for the UI (Chinese labels, seconds-based values)

/** program / media time: 4:05, 1:02:09 */
export function clock(s?: number | null): string {
  if (s === undefined || s === null || !Number.isFinite(s)) return "—";
  const t = Math.max(0, Math.floor(s));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(sec).padStart(2, "0")}`;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** wall clock HH:MM:SS from a unix time in seconds */
export function hms(t?: number | null): string {
  if (!t) return "—";
  const d = new Date(t * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function hm(t?: number | null): string {
  if (!t) return "—";
  const d = new Date(t * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const WEEK = "日一二三四五六";

/** 今天 / 昨天 / 10月4日 周六 */
export function dayLabel(t: number, now = Date.now() / 1000): string {
  const d = new Date(t * 1000), n = new Date(now * 1000);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(n) - day(d)) / 86400000);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  const s = `${d.getMonth() + 1}月${d.getDate()}日 周${WEEK[d.getDay()]}`;
  return d.getFullYear() === n.getFullYear() ? s : `${d.getFullYear()}年${s}`;
}

/** a duration in words: 45 秒, 30 分 15 秒, 1 小时 5 分 */
export function span(s?: number | null): string {
  if (s === undefined || s === null || !Number.isFinite(s)) return "—";
  const t = Math.max(0, Math.floor(s));
  if (t < 60) return `${t} 秒`;
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60;
  if (h) return `${h} 小时 ${m} 分`;
  return sec ? `${m} 分 ${sec} 秒` : `${m} 分`;
}

/** seconds with one decimal, or a dash */
export function sec1(x?: number | null): string {
  return x === undefined || x === null || !Number.isFinite(x) ? "—" : x.toFixed(1);
}

export function gb(mb?: number | null): string {
  return mb === undefined || mb === null ? "—" : (mb / 1024).toFixed(1);
}

export function median(xs: number[]): number | null {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
