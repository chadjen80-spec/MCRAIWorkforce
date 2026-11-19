import { describe, expect, it } from "vitest";
import { dictionaries } from "@/i18n/dictionaries";
import type { DashboardSummary, Job } from "./types";
import { PLANNED, WORKFLOWS, isFirstRun, offlineFor, suggestTasks, workflowById } from "./workflows";

function summary(over: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    active_jobs: 0,
    completed_jobs: 3,
    failed_jobs: 0,
    awaiting_approval_jobs: 0,
    month_cost_usd: 1,
    active_estimated_cost_usd: 0,
    monthly_budget_usd: 100,
    recent_artifacts: [],
    offline_queues: [],
    ...over,
  };
}
const art = (kind: string) => ({ kind }) as unknown as DashboardSummary["recent_artifacts"][number];

describe("quick action catalogue", () => {
  it("only offers deliverables the planner supports, worded so the rule planner recognises them", () => {
    // Mirrors the keyword rules in apps/api/app/agents/planner.py (parse_command_rules).
    const recognised: Record<string, RegExp> = {
      campaign: /images?.*captions?|ภาพ.*แคปชัน/i,
      captions: /captions?|แคปชัน/i,
      video: /video|วิดีโอ/i,
      quotation: /quotation|ใบเสนอราคา/i,
      brief: /\bbrief\b|บรีฟ/i,
      poster: /poster|โปสเตอร์/i,
    };
    for (const w of WORKFLOWS)
      for (const locale of ["th", "en"] as const) expect(dictionaries[locale][w.command]).toMatch(recognised[w.id]);
  });

  it("keeps not-yet-built Quote-to-Cash steps out of the actions", () => {
    const actionIds = WORKFLOWS.map((w) => w.id as string);
    for (const p of PLANNED) expect(actionIds).not.toContain(p.id);
    expect(actionIds).not.toContain("invoice");
  });
});

describe("offlineFor", () => {
  it("lists the queues a workflow needs that have no worker, including planner and QA", () => {
    expect(offlineFor(workflowById("video"), ["video", "image"])).toEqual(["video"]);
    expect(offlineFor(workflowById("captions"), ["planner"])).toEqual(["planner"]);
    expect(offlineFor(workflowById("quotation"), [])).toEqual([]);
    expect(offlineFor(workflowById("quotation"), undefined)).toEqual([]);
  });
});

describe("suggestTasks: what Mira suggests next", () => {
  it("puts approvals first and links straight to the filtered job list", () => {
    const [first] = suggestTasks(summary({ awaiting_approval_jobs: 2, failed_jobs: 1 }));
    expect(first).toMatchObject({ id: "review", href: "/jobs?status=awaiting_approval", vars: { n: "2" } });
  });

  it("flags failures, offline workers and a nearly used budget", () => {
    const ids = suggestTasks(summary({ failed_jobs: 1, offline_queues: ["video"], month_cost_usd: 85 }), 10).map((x) => x.id);
    expect(ids).toEqual(expect.arrayContaining(["failed", "offline", "budget"]));
  });

  it("suggests a first job to a brand-new organisation", () => {
    const fresh = summary({ completed_jobs: 0 });
    expect(isFirstRun(fresh)).toBe(true);
    expect(suggestTasks(fresh)).toEqual([expect.objectContaining({ workflow: "campaign" })]);
  });

  it("builds on existing work and never suggests a workflow whose agents are offline", () => {
    const withImages = summary({ recent_artifacts: [art("image")] });
    expect(suggestTasks(withImages, 10).map((x) => x.workflow)).toEqual(["captions", "video", "quotation"]);
    const videoDown = summary({ recent_artifacts: [art("image")], offline_queues: ["video"] });
    expect(suggestTasks(videoDown, 10).map((x) => x.workflow)).not.toContain("video");
  });

  it("returns nothing before the summary has loaded, and respects the limit", () => {
    expect(suggestTasks(null)).toEqual([]);
    expect(suggestTasks(summary({ awaiting_approval_jobs: 1, failed_jobs: 1, offline_queues: ["qa"], month_cost_usd: 99 }), 2)).toHaveLength(2);
  });

  it("links straight to the one job waiting for approval", () => {
    const waiting = { id: "j9", status: "awaiting_approval", command: "quotation for ACME" } as unknown as Job;
    const [first] = suggestTasks(summary({ awaiting_approval_jobs: 1 }), 4, { jobs: [waiting] });
    expect(first).toMatchObject({ id: "review", href: "/jobs/j9", label: "suggest.reviewOne" });
  });

  it("offers to watch a running job unless it is already on Mira's screen", () => {
    const running = { id: "r1", status: "running", command: "2 images" } as unknown as Job;
    expect(suggestTasks(summary(), 10, { jobs: [running] }).find((x) => x.watchJobId)).toMatchObject({ watchJobId: "r1" });
    expect(suggestTasks(summary(), 10, { jobs: [running], watchingJobId: "r1" }).some((x) => x.watchJobId)).toBe(false);
  });

  it("offers to send the newest failed command again, or the failed list when its agents are offline", () => {
    const failed = { id: "f1", status: "failed", command: "A 30-second short video for X", requested_agents: ["video"] } as unknown as Job;
    const retry = suggestTasks(summary({ failed_jobs: 1 }), 10, { jobs: [failed] }).find((x) => x.retryCommand);
    expect(retry).toMatchObject({ retryCommand: "A 30-second short video for X", tone: "warn" });
    const down = suggestTasks(summary({ failed_jobs: 1, offline_queues: ["video"] }), 10, { jobs: [failed] });
    expect(down.some((x) => x.retryCommand)).toBe(false);
    expect(down.find((x) => x.id === "failed")).toMatchObject({ href: "/jobs?status=failed" });
  });

  it("does not divide by a zero budget", () => {
    expect(suggestTasks(summary({ monthly_budget_usd: 0, month_cost_usd: 5 }), 10).map((x) => x.id)).not.toContain("budget");
  });
});
