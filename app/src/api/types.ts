// Shapes of the livedub.server API (livedub/server/app.py)

export type SvcName = "asr" | "mt" | "dub" | "mediamtx";
export type SvcState = "up" | "down" | "starting" | "stopping";

export interface Svc {
  up: boolean; state: SvcState; label: string; error?: string;
  boost?: string[];                              // asr: hotwords the running service was started with
  model?: string | null; model_path?: string;    // mt: local model key; dub: TTS model name
  impl?: string | null; vram_mib?: number | null; // dub: implementation line and VRAM from dubsvc /health
}

export type SessionState = "starting" | "running" | "stopping" | "ended" | "failed";

export interface Session {
  name: string; preset: string; title: string; state: SessionState; delay_s: number; dub: boolean;
  src?: string; kind?: SourceKind; mt_model?: string; is_live?: boolean;
  obs?: boolean; obs_url?: string | null;   // the optional OBS output (MediaMTX)
  storage_bytes?: number;
  media?: string | null;                    // retained video only
  created: number; started?: number; ended?: number; log: [number, string][];
}

/** link: a live or recorded stream behind a URL; file: a local video */
export type SourceKind = "link" | "file";

/** written by the playout at its first frame: PT p airs at T0 + delay + (p - p0) */
export interface Anchor { T0: number; p0: number; delay: number }

export interface Playout { pt: number; frames: number; dups: number; slate: number; events: number; max_late_ms: number }

export interface Health {
  progress?: { ranges?: [number, number][]; position?: number; done?: boolean };
  running?: Record<string, boolean>; mem_mb?: Record<string, number>;
  playout?: Partial<Playout>; anchor?: Anchor | null;
  source_error?: string | null;   // why the link cannot be opened (e.g. YouTube asks to sign in), while it is so
  source_waiting?: boolean;       // ... only because the stream has not started yet: nothing to fix
}

export interface Status {
  services: Record<SvcName, Svc>;
  gpu: { name?: string; used_mb?: number; total_mb?: number; util?: number };
  warnings: string[]; session: Session | null; health: Health; t?: number;
}

/** what a service is and where it runs on this machine (GET /api/services) */
export interface SvcInfo { what: string; where: string; port: string; boot: string; logs: string[] }

/** one line of the environment check (GET /api/doctor) */
export interface DoctorItem {
  group: string; label: string; ok: boolean; detail: string; fix: string; level: "required" | "optional" | "info";
}
export interface DoctorReport {
  items: DoctorItem[]; ok: boolean; missing_required: string[]; missing_optional: string[];
  settings_file: string; settings_exists: boolean;
}

/** where yt-dlp gets the sign-in for YouTube's bot check (GET /api/settings/youtube); the cookies never come back */
export interface YoutubeAuth { cookies: string; cookies_from_browser: string; cookies_found: boolean }

/** an online translation API (GET /api/settings/online); the key itself never comes back */
export interface Provider {
  name: string; label: string; base_url: string; model: string; has_key: boolean; key_url: string;
  problem: string | null;
}

export interface Preset {
  name: string; title: string; src: string; kind: SourceKind; problem: string | null; delay_s: number;
  domain: string; local?: boolean; mt_model?: string; mt_online?: boolean;
}

/** a translation model the backend knows (GET /api/models) */
export interface MtModel {
  key: string; label: string; detail: string; online: boolean; ready: boolean; note: string; default: boolean;
}

export interface StartOpts { preset: string; src: string; delay: number; dub: boolean; obs: boolean; mt_model: string }

export interface SetupStatus {
  state: "idle" | "running" | "done" | "failed"; message: string; error: string; model_dir?: string; demo?: string | null;
}

export interface SessionSummary {
  name: string; title: string; state: SessionState; created: number; started?: number | null;
  ended?: number | null; delay_s: number; dub: boolean;
}

/** one line of events.jsonl: the subtitle, then the same pt0 again once its dub clip exists (dub = the clip's path,
 *  gain = its level relative to the normalised dub) */
export interface EventRec { pt0: number; pt1: number; zh: string; en: string; spk?: number; dub?: string | null; gain?: number }

/** one line of timing.jsonl: a subtitle unit (has pt1) or a dub result (has dub) */
export interface TimingRec {
  pt0: number; pt1?: number; spk?: number; committed?: number; mt_s?: number; written?: number; lag_s?: number;
  slack_s?: number; dub?: DubResult; tts_s?: number; natural_s?: number; factor?: number; clamped?: boolean;
  audio_s?: number; target_s?: number; left_s?: number; err?: string;
}
export type DubResult = "ok" | "late" | "skipped" | "failed";

export interface SessionDetail {
  info: Session; events: EventRec[]; timing: TimingRec[]; anchor: Anchor | null;
}
