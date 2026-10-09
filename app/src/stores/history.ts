// Past sessions: the list from GET /api/sessions, one session's records from GET /api/sessions/{name}.
import { reactive } from "vue";
import { api } from "../api/client";
import type { SessionDetail, SessionSummary } from "../api/types";

export const history = reactive({
  list: [] as SessionSummary[],
  loaded: false,
  error: "",
  selected: "" as string,
  detail: null as SessionDetail | null,
  loading: "",                    // the name being fetched
  detailError: "",
});

export async function loadList() {
  try {
    history.list = await api.sessions();
    history.error = "";
  } catch (e) {
    history.error = (e as Error).message;
  }
  history.loaded = true;
  if (!history.selected && history.list.length) select(history.list[0].name);
}

export async function select(name: string) {
  history.selected = name;
  history.detailError = "";
  history.loading = name;
  try {
    const d = await api.session(name);
    if (history.selected === name) history.detail = d;
  } catch (e) {
    if (history.selected === name) { history.detail = null; history.detailError = (e as Error).message; }
  } finally {
    if (history.loading === name) history.loading = "";
  }
}

export async function remove(name: string, cacheOnly: boolean) {
  await api.deleteSession(name, cacheOnly);
  history.detail = null;
  if (!cacheOnly) history.selected = "";
  await loadList();
  if (cacheOnly) await select(name);
}
