import { describe, expect, it } from "vitest";
import { dictionaries } from "@/i18n/dictionaries";
import type { DashboardSummary } from "./types";
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

  it("does not divide by a zero budget", () => {
    expect(suggestTasks(summary({ monthly_budget_usd: 0, month_cost_usd: 5 }), 10).map((x) => x.id)).not.toContain("budget");
  });
});
