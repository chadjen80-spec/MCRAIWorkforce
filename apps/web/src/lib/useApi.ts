"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, toApiError } from "./api";

interface State<T> {
  key: string | null;
  data?: T;
  error?: ApiError;
}

export interface UseApiResult<T> {
  data: T | undefined;
  error: ApiError | undefined;
  /** True until the first response (or error) for the current key. */
  loading: boolean;
  reload: () => void;
}

/**
 * Loads data from the server and optionally polls it.
 * - `key` identifies the request; changing it refetches. `null` disables.
 * - `interval` (ms) > 0 polls after each response; 0 loads once.
 * - Polling pauses while the tab is hidden and refetches when it is shown again.
 * - A failed refresh keeps the last real data and reports the error alongside.
 */
export function useApi<T>(
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
  opts: { interval?: number } = {},
): UseApiResult<T> {
  const interval = opts.interval ?? 0;
  const [state, setState] = useState<State<T>>({ key: null });
  const [nonce, setNonce] = useState(0);
  const fetcherRef = useRef(fetcher);

  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  useEffect(() => {
    if (key === null) return;
    let stopped = false;
    let inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;

    const run = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      controller = new AbortController();
      try {
        const data = await fetcherRef.current(controller.signal);
        if (!stopped) setState({ key, data, error: undefined });
      } catch (e) {
        if (!stopped) {
          const error = toApiError(e);
          setState((prev) => ({ key, data: prev.key === key ? prev.data : undefined, error }));
        }
      } finally {
        inFlight = false;
        if (!stopped && interval > 0 && !document.hidden) {
          timer = setTimeout(run, interval);
        }
      }
    };

    const onVisibility = () => {
      clearTimeout(timer);
      if (!document.hidden) void run();
    };

    void run();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [key, interval, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const current = state.key === key;
  const data = current ? state.data : undefined;
  const error = current ? state.error : undefined;
  return { data, error, loading: key !== null && data === undefined && error === undefined, reload };
}
