"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * How the Assistant page is laid out:
 * - "work": Mira at a moderate size beside the operations dashboard (default);
 * - "focus": Mira large, dashboard and quick actions hidden, a one-line status strip instead.
 * The choice is a per-browser convenience; when storage is unavailable it lasts for this visit only.
 */
export type LayoutMode = "work" | "focus";
const KEY = "mcr.assistant.layout";
const EVENT = "mcr-layout-mode";
let memory: LayoutMode | null = null; // used when localStorage throws

export function readLayoutMode(): LayoutMode {
  try {
    return window.localStorage.getItem(KEY) === "focus" ? "focus" : "work";
  } catch {
    return memory ?? "work";
  }
}

export function writeLayoutMode(mode: LayoutMode): void {
  memory = mode;
  try {
    window.localStorage.setItem(KEY, mode);
  } catch {
    // private window or blocked storage: kept in memory only
  }
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange); // another tab changed it
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function useLayoutMode(): [LayoutMode, (mode: LayoutMode) => void] {
  // The server render uses "work"; the browser then shows the saved choice.
  const mode = useSyncExternalStore(subscribe, readLayoutMode, () => "work" as LayoutMode);
  const set = useCallback((next: LayoutMode) => writeLayoutMode(next), []);
  return [mode, set];
}
