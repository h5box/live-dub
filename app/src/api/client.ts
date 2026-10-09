import type { DoctorReport, MtModel, Preset, Provider, Session, SessionDetail, SessionSummary, StartOpts, Status,
  SvcInfo, SvcName, SetupStatus } from "./types";

// the backend always listens on 127.0.0.1:8090 and also serves the built UI there, so a browser can open it too
// (same origin, no base); VITE_API points the dev server elsewhere, e.g. at app/dev/mock_server.py
const sameOrigin = location.protocol.startsWith("http") && location.port === "8090";
export const BASE: string = import.meta.env.VITE_API ?? (sameOrigin ? "" : "http://127.0.0.1:8090");

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(BASE + path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("连不上后端（127.0.0.1:8090）");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((data as { detail?: string }).detail || `后端返回 ${r.status}`);
  return data as T;
}

export const api = {
  setupStatus: () => call<SetupStatus>("GET", "/api/setup"),
  install: (component: "subtitles" | "youtube" | "dub", model_dir?: string) =>
    call<SetupStatus>("POST", "/api/setup", { component, model_dir }),
  status: () => call<Status>("GET", "/api/status"),
  configs: () => call<Preset[]>("GET", "/api/configs"),
  sessions: () => call<SessionSummary[]>("GET", "/api/sessions"),
  session: (name: string) => call<SessionDetail>("GET", `/api/sessions/${encodeURIComponent(name)}`),
  deleteSession: (name: string, cacheOnly: boolean) =>
    call<{ ok: boolean }>("DELETE", `/api/sessions/${encodeURIComponent(name)}?cache_only=${cacheOnly}`),
  service: (name: SvcName, action: "start" | "stop" | "restart") =>
    call<{ ok: boolean }>("POST", `/api/services/${name}/${action}`, {}),
  models: () => call<MtModel[]>("GET", "/api/models"),
  start: (o: StartOpts) => call<Session>("POST", "/api/session/start", o),
  stop: () => call<Session>("POST", "/api/session/stop"),
  prioritize: (pt: number) => call<{ ok: boolean }>("POST", "/api/session/priority", { pt }),
  servicesInfo: () => call<Record<SvcName, SvcInfo>>("GET", "/api/services"),
  serviceLog: (name: SvcName, lines = 200) =>
    call<Record<string, string>>("GET", `/api/services/${name}/log?lines=${lines}`),
  doctor: () => call<DoctorReport>("GET", "/api/doctor"),
  providers: () => call<Provider[]>("GET", "/api/settings/online"),
  saveProvider: (p: { provider: string; base_url?: string; model?: string; api_key?: string }) =>
    call<Provider[]>("POST", "/api/settings/online", p),
  testProvider: (name: string) =>
    call<{ ok: boolean; reply?: string; wall_s?: number; error?: string }>("POST", `/api/settings/online/${name}/test`),
};

/** a run's dub clip (the events carry its path on the backend's disk; only the file name is served) */
export const clipUrl = (session: string, file: string) =>
  `${BASE}/api/sessions/${encodeURIComponent(session)}/clips/${encodeURIComponent(file)}`;
