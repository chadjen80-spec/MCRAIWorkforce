"use client";

// One snapshot of "how is the workforce doing" shared by everything on the Assistant page: Mira's spoken
// welcome briefing, the operations panel and the suggestions. They all read the same object, so the
// numbers Mira says always match the numbers on screen, and the page makes one pair of requests per
// refresh instead of one per consumer.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api, toApiError, type ApiError } from "./api";
import type { DashboardSummary, Job, JobStatus, Paginated } from "./types";

/** Jobs that are still being worked on (shown as live progress). */
export const LIVE_STATUSES: readonly JobStatus[] = ["queued", "planning", "planned", "running", "cancelling"];
/** Statuses the snapshot lists individually: live work, work waiting for a decision, recent failures. */
const LISTED: JobStatus[] = [...LIVE_STATUSES, "awaiting_approval", "failed", "partial"];
const LIST_LIMIT = 30;

export interface OpsSnapshot {
  summary: DashboardSummary;
  /** Newest first (the API's order), only the LISTED statuses. */
  jobs: Job[];
  /** When the snapshot was taken (ms since epoch). */
  at: number;
}

export async function fetchOpsSnapshot(signal?: AbortSignal): Promise<OpsSnapshot> {
  const [summary, jobs] = await Promise.all([
    api<DashboardSummary>("/v1/dashboard/summary", { signal }),
    api<Paginated<Job>>("/v1/jobs", { query: { status: LISTED, limit: LIST_LIMIT }, signal }),
  ]);
  return { summary, jobs: jobs.items, at: Date.now() };
}

export const liveJobs = (s: OpsSnapshot | undefined) => (s?.jobs ?? []).filter((j) => LIVE_STATUSES.includes(j.status));
export const waitingJobs = (s: OpsSnapshot | undefined) => (s?.jobs ?? []).filter((j) => j.status === "awaiting_approval");
export const failedJobs = (s: OpsSnapshot | undefined) => (s?.jobs ?? []).filter((j) => j.status === "failed" || j.status === "partial");

export interface UseOpsSnapshot {
  snapshot: OpsSnapshot | undefined;
  /** The last refresh failed (the previous snapshot, if any, is kept). */
  error: ApiError | undefined;
  loading: boolean;
  /** Refresh now. Concurrent calls share one request. */
  refresh: () => Promise<OpsSnapshot | undefined>;
  /** The current snapshot if it is younger than `maxAgeMs`, otherwise a fresh one. */
  get: (maxAgeMs?: number) => Promise<OpsSnapshot | undefined>;
}

export function useOpsSnapshot(
  { interval = 15000, load = fetchOpsSnapshot }: { interval?: number; load?: (signal?: AbortSignal) => Promise<OpsSnapshot> } = {},
): UseOpsSnapshot {
  const [snapshot, setSnapshot] = useState<OpsSnapshot>();
  const [error, setError] = useState<ApiError>();
  const latest = useRef<OpsSnapshot | undefined>(undefined);
  const inflight = useRef<Promise<OpsSnapshot | undefined> | null>(null);
  const alive = useRef(true);
  const loadRef = useRef(load);
  useEffect(() => {
    loadRef.current = load;
  });

  const refresh = useCallback((): Promise<OpsSnapshot | undefined> => {
    if (inflight.current) return inflight.current;
    const run = loadRef
      .current()
      .then((s) => {
        latest.current = s;
        if (alive.current) {
          setSnapshot(s);
          setError(undefined);
        }
        return s;
      })
      .catch((e: unknown) => {
        if (alive.current) setError(toApiError(e));
        return latest.current; // keep showing the last real data
      })
      .finally(() => {
        inflight.current = null;
      });
    inflight.current = run;
    return run;
  }, []);

  const get = useCallback(
    (maxAgeMs = interval) => {
      const s = latest.current;
      return s && Date.now() - s.at < maxAgeMs ? Promise.resolve(s) : refresh();
    },
    [interval, refresh],
  );

  useEffect(() => {
    alive.current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => {
      void refresh().finally(() => {
        // Paused while the tab is hidden; `wake` refreshes and resumes when it is shown again.
        if (alive.current && interval > 0 && !document.hidden) timer = setTimeout(tick, interval);
      });
    };
    const wake = () => {
      if (document.hidden) return;
      clearTimeout(timer);
      tick();
    };
    tick();
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);
    return () => {
      alive.current = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", wake);
    };
  }, [interval, refresh]);

  return { snapshot, error, loading: snapshot === undefined && error === undefined, refresh, get };
}

function subscribeOnline(onChange: () => void) {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** Whether the browser reports a network connection (updates on online/offline events). */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribeOnline, () => navigator.onLine !== false, () => true);
}
