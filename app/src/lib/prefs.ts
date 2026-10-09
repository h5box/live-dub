// Per-window conveniences kept in localStorage (theme, last form values, view). Storage can be missing or
// throw (private profile, cleared data): everything falls back to defaults.
import { ref, watch } from "vue";

export function load<T>(key: string, fallback: T): T {
  try {
    const v = localStorage.getItem("livedub." + key);
    return v === null ? fallback : (JSON.parse(v) as T);
  } catch {
    return fallback;
  }
}

export function save(key: string, value: unknown) {
  try { localStorage.setItem("livedub." + key, JSON.stringify(value)); } catch { /* not persisted */ }
}

export type Theme = "system" | "light" | "dark";
export const theme = ref<Theme>(load<Theme>("theme", "system"));

function apply(t: Theme) {
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}
apply(theme.value);
watch(theme, (t) => { apply(t); save("theme", t); });

export function cycleTheme() {
  theme.value = theme.value === "system" ? "light" : theme.value === "light" ? "dark" : "system";
}
