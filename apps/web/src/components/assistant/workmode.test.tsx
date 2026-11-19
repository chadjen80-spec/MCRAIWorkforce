// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "@/i18n/I18nProvider";
import { dictionaries } from "@/i18n/dictionaries";
import { ApiError } from "@/lib/api";
import type { DashboardSummary, Job } from "@/lib/types";
import { FocusStrip, ModeToggle } from "./ModeToggle";
import { OpsSummary, TaskSuggestions, timeAgo } from "./OpsPanel";

const th = dictionaries.th;
const wrap = (ui: React.ReactNode) => render(<I18nProvider>{ui}</I18nProvider>);
afterEach(cleanup);

const summary: DashboardSummary = {
  active_jobs: 1,
  completed_jobs: 4,
  failed_jobs: 0,
  awaiting_approval_jobs: 1,
  month_cost_usd: 2,
  active_estimated_cost_usd: 0,
  monthly_budget_usd: 50,
  recent_artifacts: [],
  offline_queues: [],
};
const NOW = Date.parse("2026-11-20T12:00:00Z");
const job = (over: Partial<Job>) =>
  ({ id: "j", command: "cmd", status: "running", progress: 40, created_at: "2026-11-20T09:00:00Z", finished_at: null,
     qa_verdict: null, demo_mode: false, estimated_cost_usd: null, actual_cost_usd: null, ...over }) as unknown as Job;

describe("ModeToggle", () => {
  it("is a labelled pair of toggle buttons that switches the layout", () => {
    const onChange = vi.fn();
    wrap(<ModeToggle mode="work" onChange={onChange} />);
    const group = screen.getByRole("group", { name: th["mode.label"] });
    const [work, focus] = group.querySelectorAll("button");
    expect(work.getAttribute("aria-pressed")).toBe("true");
    expect(focus.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(focus);
    expect(onChange).toHaveBeenCalledWith("focus");
  });

  it("Focus mode keeps the counts visible with a way back to the dashboard", () => {
    const onShow = vi.fn();
    wrap(<FocusStrip waiting={3} live={1} onShowDashboard={onShow} />);
    const link = screen.getByRole("link", { name: th["mode.focusStrip"].replace("{waiting}", "3").replace("{live}", "1") });
    expect(link.getAttribute("href")).toBe("/jobs?status=awaiting_approval");
    fireEvent.click(screen.getByRole("button", { name: th["mode.showDashboard"] }));
    expect(onShow).toHaveBeenCalled();
  });
});

describe("OpsSummary states", () => {
  it("shows a busy loading state before the first snapshot", () => {
    wrap(<OpsSummary summary={undefined} waiting={undefined} loading />);
    expect(screen.getByText(th["ops.loading"])).toBeTruthy();
    expect(screen.getByRole("region", { name: th["ops.title"] }).getAttribute("aria-busy")).toBe("true");
  });

  it("explains a failed first load and offers a retry", () => {
    const onRetry = vi.fn();
    wrap(<OpsSummary summary={undefined} waiting={undefined} error={new ApiError(0, "NETWORK_ERROR", "down")} onRetry={onRetry} />);
    expect(screen.getByRole("alert").textContent).toContain(th["ops.loadFailed"]);
    fireEvent.click(screen.getByRole("button", { name: th["ops.retry"] }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("keeps the last figures after a failed refresh and says how old they are", () => {
    wrap(<OpsSummary summary={summary} waiting={[]} error={new ApiError(0, "NETWORK_ERROR", "down")} takenAt={NOW} onRetry={() => {}} now={NOW} />);
    expect(screen.getByRole("alert").textContent).toMatch(/อัปเดตไม่สำเร็จ/);
    expect(screen.getByText(th["ops.awaiting"])).toBeTruthy(); // figures still shown
  });
});

describe("OpsSummary live jobs and approvals", () => {
  it("lists live jobs with real progress and a Watch live button", () => {
    const onWatch = vi.fn();
    wrap(<OpsSummary summary={summary} waiting={[]} live={[job({ id: "r1", command: "2 images" })]} onWatch={onWatch} now={NOW} />);
    expect(screen.getByRole("progressbar", { name: /2 images/ }).getAttribute("aria-valuenow")).toBe("40");
    fireEvent.click(screen.getByRole("button", { name: `${th["ops.watch"]}: 2 images` }));
    expect(onWatch).toHaveBeenCalledWith("r1");
  });

  it("marks the job already on Mira's screen instead of offering it again", () => {
    wrap(<OpsSummary summary={summary} waiting={[]} live={[job({ id: "r1" })]} onWatch={() => {}} watchingJobId="r1" now={NOW} />);
    expect(screen.queryByRole("button", { name: new RegExp(th["ops.watch"]) })).toBeNull();
    expect(screen.getByText(th["ops.watching"])).toBeTruthy();
  });

  it("shows how long a job has waited and its QA verdict, from real fields only", () => {
    const waiting = [
      job({ id: "w1", status: "awaiting_approval", command: "captions", finished_at: "2026-11-20T09:00:00Z", qa_verdict: "HUMAN_REVIEW", demo_mode: true }),
      job({ id: "w2", status: "awaiting_approval", command: "brief", finished_at: "2026-11-20T11:30:00Z", qa_verdict: "PASS" }),
    ];
    wrap(<OpsSummary summary={summary} waiting={waiting} waitingTotal={2} now={NOW} />);
    expect(screen.getByText(new RegExp(timeAgo("2026-11-20T09:00:00Z", NOW, "th")))).toBeTruthy();
    expect(screen.getByText(th["ops.qaCheck"])).toBeTruthy();
    expect(screen.getByText(th["ops.qaPass"])).toBeTruthy();
    // no invented deadline or priority
    expect(document.body.textContent).not.toMatch(/deadline|priority|กำหนดส่ง|ด่วน/i);
  });
});

describe("TaskSuggestions actions", () => {
  it("watches a running job and puts a failed command back in the composer", () => {
    const onWatch = vi.fn();
    const onRetry = vi.fn();
    const jobs = [job({ id: "r1", command: "2 images" }), job({ id: "f1", status: "failed", command: "A quotation PDF for ACME", requested_agents: ["document"] } as Partial<Job>)];
    wrap(<TaskSuggestions summary={{ ...summary, awaiting_approval_jobs: 0, failed_jobs: 1 }} onStart={() => {}} jobs={jobs} onWatch={onWatch} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: th["suggest.watch"].replace("{cmd}", "2 images") }));
    expect(onWatch).toHaveBeenCalledWith("r1");
    fireEvent.click(screen.getByRole("button", { name: th["suggest.retry"].replace("{cmd}", "A quotation PDF for ACME") }));
    expect(onRetry).toHaveBeenCalledWith("A quotation PDF for ACME");
  });

  it("hides actions the page cannot perform", () => {
    wrap(<TaskSuggestions summary={summary} onStart={() => {}} jobs={[job({ id: "r1" })]} />);
    expect(screen.queryByRole("button", { name: /cmd/ })).toBeNull();
  });
});
