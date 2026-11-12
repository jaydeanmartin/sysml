import { useSyncExternalStore } from "react";

/** The UI color theme. Stored per browser; defaults to the OS preference on first visit. */
export type Theme = "light" | "dark";

const THEME_KEY = "studio.theme";
const listeners = new Set<() => void>();

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

let current: Theme = initialTheme();

function apply(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}
apply(current);

export function getTheme(): Theme {
  return current;
}

export function setTheme(theme: Theme) {
  if (theme === current) return;
  current = theme;
  apply(theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* storage unavailable */
  }
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The current theme; re-renders the caller when it changes. */
export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, getTheme);
}

/** Light/dark switch for the topbar. */
export function ThemeSwitch() {
  const theme = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      className="theme-switch"
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      <span className="theme-icon" aria-hidden>☀</span>
      <span className="track" aria-hidden>
        <span className="knob" />
      </span>
      <span className="theme-icon" aria-hidden>☾</span>
      <span className="sr-only">Dark mode</span>
    </button>
  );
}
