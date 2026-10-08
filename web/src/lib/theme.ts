/**
 * What: the light or dark theme the person picked, kept in the browser.
 *
 * Why: the app follows the operating system's theme, but a presenter may want the light theme
 * on a projector while the laptop is dark (or the other way round).
 *
 * How: the choice is stored in localStorage and written to `data-theme` on <html>, which
 * globals.css reads. THEME_SCRIPT runs in <head> before the page is drawn, so a stored choice
 * applies from the first paint. Storage can be off (a private window), so every access is
 * guarded and the theme then simply follows the system. `subscribeTheme` lets a component follow
 * the theme on screen (with React's useSyncExternalStore).
 */

export type Theme = "light" | "dark";

export const THEME_KEY = "avtalsanalys-theme";

export const THEME_SCRIPT = `try{var t=localStorage.getItem("${THEME_KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

/** The theme on screen now: the stored choice, or the system's. */
export function currentTheme(): Theme {
  const chosen = document.documentElement.dataset.theme;
  if (chosen === "light" || chosen === "dark") return chosen;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

const CHANGE_EVENT = "avtalsanalys-theme-change";

/** Calls `onChange` when the theme on screen changes; returns a function that stops it. */
export function subscribeTheme(onChange: () => void): () => void {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    media.removeEventListener("change", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  window.dispatchEvent(new Event(CHANGE_EVENT));
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Not stored: the choice lasts until the page is reloaded.
  }
}
