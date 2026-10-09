import { BASE } from "./client";
import type { EventRec, Status, TimingRec } from "./types";

export interface StreamHandlers {
  status(s: Status): void;
  /** the session the records belong to changed (or the stream reconnected): drop what you have */
  reset(name: string | null): void;
  event(rows: EventRec[]): void;
  timing(rows: TimingRec[]): void;
  link(ok: boolean): void;
}

/** GET /api/stream (SSE): status every 2 s, then the current session's records as they are written.
 *  EventSource reconnects by itself (the server asks for 2 s); every reconnect starts with a reset. */
export function subscribe(h: StreamHandlers): () => void {
  const es = new EventSource(BASE + "/api/stream");
  const on = <T>(kind: string, f: (d: T) => void) =>
    es.addEventListener(kind, (e) => {
      try { f(JSON.parse((e as MessageEvent).data)); } catch (err) { console.error(kind, err); }
    });
  es.onopen = () => h.link(true);
  es.onerror = () => h.link(false);
  on<Status>("status", h.status);
  on<{ name: string | null }>("reset", (d) => h.reset(d.name));
  on<EventRec[]>("event", h.event);
  on<TimingRec[]>("timing", h.timing);
  return () => es.close();
}
