import { useSyncExternalStore } from "react";

/** The viewer's colour theme: "system" follows the OS. The choice is kept in
 *  the browser and applied as data-theme on <html> (none for "system"), which
 *  selects the token set in styles.css. index.html applies it before the app
 *  loads, so the page does not flash light first. */
export type ThemePref = "system" | "light" | "dark";
export type Theme = "light" | "dark";

const KEY = "studio.theme";
const media = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
const listeners = new Set<() => void>();

function stored(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system"; // storage blocked: follow the OS
  }
}

let pref: ThemePref = stored();

function apply(p: ThemePref) {
  const el = document.documentElement;
  if (p === "system") delete el.dataset.theme;
  else el.dataset.theme = p;
}

export function setThemePref(p: ThemePref) {
  pref = p;
  try {
    if (p === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, p);
  } catch {
    /* not remembered, still applied */
  }
  apply(p);
  listeners.forEach((l) => l());
}

function resolve(): Theme {
  return pref === "system" ? (media?.matches ? "dark" : "light") : pref;
}

function subscribe(l: () => void) {
  listeners.add(l);
  media?.addEventListener("change", l);
  return () => {
    listeners.delete(l);
    media?.removeEventListener("change", l);
  };
}

/** The theme chosen and the one in effect; re-renders when either changes. */
export function useTheme(): { pref: ThemePref; theme: Theme; setPref(p: ThemePref): void } {
  const key = useSyncExternalStore(subscribe, () => `${pref}|${resolve()}`);
  const [p, t] = key.split("|") as [ThemePref, Theme];
  return { pref: p, theme: t, setPref: setThemePref };
}

/** A CSS custom property's current value (for colours drawn outside CSS, e.g. three.js). */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
