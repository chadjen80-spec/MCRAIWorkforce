import { describe, expect, it } from "vitest";
import {
  WIDE,
  actualCost,
  aiProgress,
  approvalState,
  buildGraphModel,
  fitView,
  graphBounds,
  hubState,
  masterState,
  taskSeconds,
  taskState,
} from "./agentGraph";
import type { Job, Task } from "./types";

const job = (over: Partial<Job> = {}) =>
  ({ id: "j", status: "running", progress: 40, requested_agents: [], require_approval: false, approval_status: null, qa_verdict: null, ...over }) as Job;
const task = (key: string, agent: Task["agent"], over: Partial<Task> = {}) =>
  ({ id: key, key, agent, title: key, status: "pending", attempt: 1, max_attempts: 3, depends_on: [], provider: null, execution_location: null,
     routing_reason: null, error_code: null, error_message: null, estimated_cost_usd: null, actual_cost_usd: null,
     queued_at: null, started_at: null, finished_at: null, ...over }) as Task;

const campaign = [
  task("plan", "planner", { status: "succeeded" }),
  task("img_1", "image", { status: "succeeded", depends_on: ["plan"] }),
  task("img_2", "image", { status: "running", depends_on: ["plan", "img_1"] }),
  task("captions", "content", { status: "queued", depends_on: ["plan", "img_1", "img_2"] }),
  task("qa_review", "qa", { depends_on: ["img_1", "img_2", "captions"] }),
];

describe("buildGraphModel: dependencies from depends_on", () => {
  const model = buildGraphModel(job(), campaign);

  it("draws one edge per agent pair, including edges into QA, and never from the planner", () => {
    const edges = Object.fromEntries(model.edges.map((e) => [`${e.from}>${e.to}`, e.links]));
    expect(Object.keys(edges).sort()).toEqual(["content>qa", "image>content", "image>qa"]);
    expect(edges["image>content"]).toEqual(["img_1>captions", "img_2>captions"]);
    expect(model.edges.some((e) => (e.from as string) === "planner")).toBe(false);
  });

  it("keeps same-agent dependencies out of the lines but available for the details panel", () => {
    expect(model.internal.get("image")).toEqual(["img_1>img_2"]);
  });

  it("shows only agents with tasks once planned, and the requested agents before that", () => {
    expect(model.visible).toEqual(["content", "image", "qa"]);
    const early = buildGraphModel(job({ status: "planning", requested_agents: ["video"] as Job["requested_agents"] }), [task("plan", "planner", { status: "running" })]);
    expect(early.visible).toEqual(["video", "qa"]);
  });
});

describe("node states", () => {
  it("marks a task running again after a failure or a QA revision as retrying", () => {
    expect(taskState(task("a", "image", { status: "queued", attempt: 2 }))).toBe("retrying");
    expect(taskState(task("a", "image", { status: "pending", attempt: 2 }))).toBe("retrying");
    expect(taskState(task("a", "image", { status: "succeeded", attempt: 2 }))).toBe("done");
    expect(taskState(task("a", "image", { status: "failed", attempt: 3 }))).toBe("failed");
  });

  it("an agent shows failed before retrying before working", () => {
    const failed = task("a", "image", { status: "failed" });
    const retry = task("b", "image", { status: "running", attempt: 2 });
    const run = task("c", "image", { status: "running" });
    expect(hubState([run, retry, failed])).toBe("failed");
    expect(hubState([run, retry])).toBe("retrying");
    expect(hubState([run])).toBe("working");
  });

  it("the Master Bot shows AI processing only; approval is separate", () => {
    expect(masterState(job({ status: "awaiting_approval" }), undefined)).toBe("done");
    expect(masterState(job({ status: "rejected" }), undefined)).toBe("done");
    expect(approvalState(job({ status: "awaiting_approval", approval_status: "pending" }))).toBe("waiting");
    expect(approvalState(job({ status: "rejected", approval_status: "rejected" }))).toBe("rejected");
    expect(approvalState(job({ status: "completed", require_approval: false }))).toBe("not_required");
    expect(approvalState(job({ status: "running", require_approval: true }))).toBe("after_ai");
    expect(approvalState(job({ status: "failed" }))).toBe("skipped");
  });

  it("AI progress counts deliverable and QA tasks, not the planner", () => {
    const p = aiProgress(job({ progress: 39.6 }), [...campaign, task("vid", "video", { status: "failed" })]);
    expect(p).toEqual({ total: 5, done: 1, failed: 1, running: 1, retrying: 0, pct: 40 });
  });
});

describe("layout: fit to the nodes actually drawn", () => {
  it("contains every visible hub with its satellites and label", () => {
    const model = buildGraphModel(job(), campaign);
    const b = graphBounds(model, WIDE);
    for (const agent of model.visible) {
      const p = WIDE.hubs[agent];
      expect(p.x).toBeGreaterThan(b.x);
      expect(p.x).toBeLessThan(b.x + b.w);
      expect(p.y + WIDE.r.hub).toBeLessThan(b.y + b.h);
    }
    expect(b.x).toBeLessThanOrEqual(WIDE.master.x - WIDE.labelW / 2);
  });

  it("is tighter when fewer agents are involved", () => {
    const all = graphBounds(buildGraphModel(job(), [...campaign, task("doc", "document"), task("vid", "video")]), WIDE);
    const two = graphBounds(buildGraphModel(job(), [task("plan", "planner"), task("doc", "document"), task("qa_review", "qa")]), WIDE);
    expect(two.w * two.h).toBeLessThan(all.w * all.h);
    expect(two.x).toBeGreaterThan(all.x); // no empty space where content/image would be
  });

  it("centres the view on the bounds with padding", () => {
    const v = fitView({ x: 100, y: 50, w: 400, h: 300 }, 20);
    expect(v).toEqual({ x: 80, y: 30, w: 440, h: 340 });
    expect(v.x + v.w / 2).toBe(300);
    const tiny = fitView({ x: 0, y: 0, w: 10, h: 10 }, 0, 260);
    expect(tiny.w).toBe(260);
    expect(tiny.x + tiny.w / 2).toBe(5);
  });
});

describe("timings and costs from the API only", () => {
  it("execution time uses real timestamps, and is null before a task starts", () => {
    expect(taskSeconds({ started_at: "2026-11-20T10:00:00Z", finished_at: "2026-11-20T10:01:05Z" }, 0)).toBe(65);
    expect(taskSeconds({ started_at: "2026-11-20T10:00:00Z", finished_at: null }, Date.parse("2026-11-20T10:00:30Z"))).toBe(30);
    expect(taskSeconds({ started_at: null, finished_at: null }, 0)).toBeNull();
  });

  it("actual cost is the sum of reported costs, or null when none is reported", () => {
    expect(actualCost([task("a", "image", { actual_cost_usd: 0.04 }), task("b", "image", { actual_cost_usd: 0.02 })])).toBeCloseTo(0.06);
    expect(actualCost([task("a", "image", { estimated_cost_usd: 0.04 })])).toBeNull();
  });
});
