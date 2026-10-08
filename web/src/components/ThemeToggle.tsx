"use client";
/**
 * What: a button in the header that switches between the light and the dark theme.
 *
 * Why: the app follows the operating system's theme, but a presenter may want the other one,
 * for example the light theme on a projector (see lib/theme.ts).
 *
 * How: the button shows the theme it switches to. Which theme is on screen is only known in the
 * browser, so the button is hidden in the server's HTML and appears once the page runs.
 */
import { useSyncExternalStore } from "react";

import { applyTheme, currentTheme, subscribeTheme } from "@/lib/theme";
import type { Theme } from "@/lib/theme";

import { Icon } from "./icons";
import styles from "./AgentApp.module.css";

export function ThemeToggle() {
  const theme = useSyncExternalStore<Theme | null>(subscribeTheme, currentTheme, () => null);

  const next: Theme = theme === "dark" ? "light" : "dark";
  const label = next === "dark" ? "Mörkt tema" : "Ljust tema";
  return (
    <button
      type="button"
      className={styles.iconButton}
      onClick={() => applyTheme(next)}
      aria-label={label}
      title={label}
      hidden={theme === null}
    >
      <Icon name={next === "dark" ? "moon" : "sun"} size={17} />
    </button>
  );
}
