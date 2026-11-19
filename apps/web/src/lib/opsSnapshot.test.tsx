// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dictionaries, type MessageKey } from "@/i18n/dictionaries";
import { budgetFigures, workforceBriefing } from "./briefing";
import { readLayoutMode, writeLayoutMode } from "./layoutMode";
import { fetchOpsSnapshot, liveJobs, useOnline, useOpsSnapshot, waitingJobs, type OpsSnapshot } from "./opsSnapshot";
import type { DashboardSummary, Job } from "./types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const summary: DashboardSummary = {
  active_jobs: 2,
  completed_jobs: 9,
  failed_jobs: 1,
  awaiting_approval_jobs: 3,
  month_cost_usd: 12.345,
  active_estimated_cost_usd: 0,
  monthly_budget_usd: 50,
  recent_artifacts: [],
  offline_queues: [],
};
const job = (id: string, status: Job["status"]) => ({ id, status, command: id }) as unknown as Job;
const snap = (at = Date.now()): OpsSnapshot => ({ summary, jobs: [job("a", "running"), job("b", "awaiting_approval")], at });

describe("useOpsSnapshot: one snapshot for the briefing and the panel", () => {
  it("loads once on mount and shares one request between concurrent callers", async () => {
    let resolve: (s: OpsSnapshot) => void = () => {};
    const load = vi.fn(() => new Promise<OpsSnapshot>((r) => (resolve = r)));
    const { result } = renderHook(() => useOpsSnapshot({ interval: 0, load }));
    expect(result.current.loading).toBe(true);
    let a: Promise<unknown> = Promise.resolve(), b: Promise<unknown> = Promise.resolve();
    act(() => {
      a = result.current.refresh();
      b = result.current.get(0);
    });
    expect(load).toHaveBeenCalledTimes(1);
    const s = snap();
    await act(async () => resolve(s));
    await expect(a).resolves.toBe(s);
    await expect(b).resolves.toBe(s);
    expect(result.current.snapshot).toBe(s); // the briefing (get) and the panel (snapshot) see the same object
  });

  it("get() reuses a fresh snapshot and fetches again only when it is too old", async () => {
    const load = vi.fn(async () => snap());
    const { result } = renderHook(() => useOpsSnapshot({ interval: 0, load }));
    await waitFor(() => expect(result.current.snapshot).toBeDefined());
    await act(async () => void (await result.current.get(60000)));
    expect(load).toHaveBeenCalledTimes(1);
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 120000);
    await act(async () => void (await result.current.get(60000)));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps the last real snapshot when a refresh fails, and reports the error", async () => {
    const first = snap();
    const load = vi.fn().mockResolvedValueOnce(first).mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useOpsSnapshot({ interval: 0, load }));
    await waitFor(() => expect(result.current.snapshot).toBe(first));
    await act(async () => void (await result.current.refresh()));
    expect(result.current.snapshot).toBe(first);
    expect(result.current.error).toBeDefined();
    expect(result.current.loading).toBe(false);
  });

  it("asks the API for live, waiting and failed jobs in one list request", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        const body = url.includes("/dashboard/summary") ? summary : { items: [], total: 0, limit: 30, offset: 0 };
        return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
      }),
    );
    await fetchOpsSnapshot();
    expect(urls).toHaveLength(2);
    const list = new URL(urls.find((u) => u.includes("/v1/jobs"))!, "http://x");
    expect(list.searchParams.getAll("status")).toEqual(expect.arrayContaining(["running", "awaiting_approval", "failed"]));
    expect(list.searchParams.get("limit")).toBe("30");
  });

  it("splits the listed jobs into live and waiting", () => {
    const s = snap();
    expect(liveJobs(s).map((j) => j.id)).toEqual(["a"]);
    expect(waitingJobs(s).map((j) => j.id)).toEqual(["b"]);
  });
});

describe("useOnline", () => {
  it("follows the browser's online and offline events", () => {
    let online = true;
    vi.spyOn(navigator, "onLine", "get").mockImplementation(() => online);
    const { result } = renderHook(() => useOnline());
    expect(result.current).toBe(true);
    act(() => {
      online = false;
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current).toBe(false);
    act(() => {
      online = true;
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current).toBe(true);
  });
});

describe("workforceBriefing: Mira says what the panel shows", () => {
  const t = (key: MessageKey, vars?: Record<string, string>) =>
    Object.entries(vars ?? {}).reduce((text, [k, v]) => text.replaceAll(`{${k}}`, v), dictionaries.en[key]);

  it("uses the same counts and budget figures as the operations panel", () => {
    const text = workforceBriefing(t, "Pim", summary);
    const { spent, budget } = budgetFigures(summary);
    expect(text).toContain("Hello Pim");
    expect(text).toContain("2 job(s) running");
    expect(text).toContain("3 waiting for your approval");
    expect(text).toContain(`$${spent} of your $${budget}`);
    expect(spent).toBe("12.35"); // the panel shows exactly this string too
  });

  it("only greets when there is no snapshot", () => {
    expect(workforceBriefing(t, "", null)).toBe(dictionaries.en["brief.helloNoName"]);
  });
});

describe("layout mode preference", () => {
  it("is remembered, and survives storage that throws", () => {
    writeLayoutMode("focus");
    expect(readLayoutMode()).toBe("focus");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    writeLayoutMode("work");
    expect(readLayoutMode()).toBe("work");
  });
});
