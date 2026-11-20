// Agent Graph model: everything the graph draws, derived only from the job and task responses the API
// already returns. No agent metadata is invented: a value the API does not provide is left out.

import type { Agent, Job, Task, TaskStatus } from "./types";

export type NodeState = "idle" | "queued" | "working" | "retrying" | "done" | "failed" | "off";
export type HubAgent = Exclude<Agent, "planner">;
export const HUB_ORDER: HubAgent[] = ["content", "image", "document", "video", "qa"];

export interface Point {
  x: number;
  y: number;
}
export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}
export interface Layout {
  w: number;
  h: number;
  master: Point;
  hubs: Record<HubAgent, Point>;
  r: { master: number; hub: number; sat: number; orbit: number };
  font: number;
  labelW: number;
}

export const WIDE: Layout = {
  w: 1000,
  h: 770,
  master: { x: 500, y: 300 },
  hubs: {
    content: { x: 180, y: 172 },
    image: { x: 215, y: 535 },
    document: { x: 820, y: 172 },
    video: { x: 785, y: 535 },
    qa: { x: 500, y: 625 },
  },
  r: { master: 84, hub: 58, sat: 17, orbit: 112 },
  font: 14,
  labelW: 210,
};

export const NARROW: Layout = {
  w: 420,
  h: 955,
  master: { x: 210, y: 372 },
  hubs: {
    content: { x: 108, y: 127 },
    image: { x: 108, y: 645 },
    document: { x: 312, y: 127 },
    video: { x: 312, y: 645 },
    qa: { x: 210, y: 835 },
  },
  r: { master: 66, hub: 46, sat: 13, orbit: 80 },
  font: 13,
  labelW: 150,
};

const TERMINAL_TASK: readonly TaskStatus[] = ["succeeded", "failed", "skipped", "cancelled"];

/** A task that is running again after a failed attempt or a QA revision (attempt > 1, not finished). */
export function isRetrying(task: Task): boolean {
  return task.attempt > 1 && !TERMINAL_TASK.includes(task.status);
}

export function taskState(task: Task): NodeState {
  if (isRetrying(task)) return "retrying";
  switch (task.status) {
    case "running":
      return "working";
    case "queued":
      return "queued";
    case "succeeded":
      return "done";
    case "failed":
      return "failed";
    case "skipped":
    case "cancelled":
      return "off";
    default:
      return "idle";
  }
}

/** One agent's state from its tasks: failures first, then retries, work in progress, waiting, done. */
export function hubState(tasks: readonly Task[]): NodeState {
  if (tasks.length === 0) return "idle";
  const states = tasks.map(taskState);
  if (states.includes("failed")) return "failed";
  if (states.includes("retrying")) return "retrying";
  if (states.includes("working")) return "working";
  if (states.includes("queued")) return "queued";
  if (states.every((s) => s === "done")) return "done";
  if (states.every((s) => s === "off")) return "off";
  if (states.some((s) => s === "done")) return "working"; // part-way through its tasks
  return "idle";
}

/**
 * The Master Bot shows AI processing only. Human approval is shown separately (see approvalState), so a
 * job waiting for review or rejected by a person still shows its AI work as done.
 */
export function masterState(job: Job, planner: Task | undefined): NodeState {
  switch (job.status) {
    case "queued":
    case "planned":
      return "queued";
    case "planning":
    case "running":
    case "cancelling":
      return "working";
    case "awaiting_approval":
    case "completed":
    case "rejected":
      return "done";
    case "failed":
      return "failed";
    case "partial":
      return planner?.status === "failed" ? "failed" : "done";
    default:
      return "off";
  }
}

export interface AiProgress {
  /** Deliverable and QA tasks (the planner is not counted). */
  total: number;
  done: number;
  failed: number;
  running: number;
  retrying: number;
  /** The job's own progress figure from the API. */
  pct: number;
}

export function aiProgress(job: Job, tasks: readonly Task[]): AiProgress {
  const work = tasks.filter((t) => t.agent !== "planner");
  return {
    total: work.length,
    done: work.filter((t) => t.status === "succeeded").length,
    failed: work.filter((t) => t.status === "failed").length,
    running: work.filter((t) => t.status === "running").length,
    retrying: work.filter(isRetrying).length,
    pct: Math.round(job.progress),
  };
}

/** Human approval, separate from AI processing, from job fields only. */
export type ApprovalState = "not_required" | "after_ai" | "waiting" | "approved" | "rejected" | "skipped";

export function approvalState(job: Job): ApprovalState {
  if (job.approval_status === "approved") return "approved";
  if (job.approval_status === "rejected" || job.status === "rejected") return "rejected";
  if (job.status === "awaiting_approval") return "waiting";
  if (job.status === "completed") return job.require_approval ? "approved" : "not_required";
  if (["failed", "partial", "cancelled"].includes(job.status)) return "skipped"; // nothing to approve
  // Still processing: a review follows when the job asks for one, or when QA escalates.
  return job.require_approval ? "after_ai" : "not_required";
}

export interface Edge {
  from: HubAgent;
  to: HubAgent;
  /** The task-level dependencies behind this edge, "fromKey>toKey". */
  links: string[];
}

export interface GraphModel {
  byAgent: Map<Agent, Task[]>;
  visible: HubAgent[];
  planner: Task | undefined;
  /** Dependencies between agents, from each task's `depends_on` (including those into QA). */
  edges: Edge[];
  /** Dependencies between tasks of the same agent (shown in the details panel, not as lines). */
  internal: Map<HubAgent, string[]>;
}

export function buildGraphModel(job: Job, tasks: readonly Task[]): GraphModel {
  const byAgent = new Map<Agent, Task[]>();
  for (const task of tasks) byAgent.set(task.agent, [...(byAgent.get(task.agent) ?? []), task]);
  const planned = tasks.some((x) => x.agent !== "planner");
  // Before the plan exists, show the agents the user asked for (and QA) as idle hubs.
  const visible = HUB_ORDER.filter((a) => (planned ? byAgent.has(a) : a === "qa" || job.requested_agents.includes(a as never)));
  const byKey = new Map(tasks.map((x) => [x.key, x]));
  const edges = new Map<string, Edge>();
  const internal = new Map<HubAgent, string[]>();
  for (const task of tasks) {
    if (task.agent === "planner") continue; // every task follows the plan: drawn as the Master Bot's beams
    for (const key of task.depends_on) {
      const from = byKey.get(key);
      if (!from || from.agent === "planner") continue;
      const link = `${from.key}>${task.key}`;
      if (from.agent === task.agent) {
        internal.set(task.agent, [...(internal.get(task.agent) ?? []), link]);
        continue;
      }
      const id = `${from.agent}>${task.agent}`;
      const edge = edges.get(id) ?? { from: from.agent as HubAgent, to: task.agent as HubAgent, links: [] };
      edge.links.push(link);
      edges.set(id, edge);
    }
  }
  return { byAgent, visible, planner: byAgent.get("planner")?.[0], edges: [...edges.values()], internal };
}

/** Where task satellites sit around a hub that has more than one task. */
export function satellitePoints(center: Point, count: number, orbit: number): Point[] {
  return Array.from({ length: count }, (_, i) => {
    const angle = -Math.PI / 2 + (i / count) * Math.PI * 2;
    return { x: center.x + Math.cos(angle) * orbit, y: center.y + Math.sin(angle) * orbit };
  });
}

/** The extent a node needs, including its satellites, selection ring and label underneath. */
function nodeBox(p: Point, radius: number, labelTop: number, L: Layout): Box {
  const half = Math.max(radius, L.labelW / 2);
  const labelBottom = p.y + labelTop + 8 + L.font * 2 + 16;
  return { x: p.x - half, y: p.y - radius, w: half * 2, h: labelBottom - (p.y - radius) };
}

const union = (a: Box, b: Box): Box => {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return { x, y, w: Math.max(a.x + a.w, b.x + b.w) - x, h: Math.max(a.y + a.h, b.y + b.h) - y };
};

/** The bounding box of every node actually drawn (master, visible hubs, satellites, labels). */
export function graphBounds(model: GraphModel, L: Layout): Box {
  let box = nodeBox(L.master, L.r.master * 1.22, L.r.master * 0.78 * 1.5, L);
  for (const agent of model.visible) {
    const n = model.byAgent.get(agent)?.length ?? 0;
    const radius = n > 1 ? L.r.orbit + L.r.sat * 1.22 : L.r.hub * 1.22;
    const labelTop = (n > 1 ? (L.r.orbit + L.r.sat) / 1.5 : L.r.hub * 0.78) * 1.5;
    box = union(box, nodeBox(L.hubs[agent], radius, labelTop, L));
  }
  return box;
}

/** A view box around `bounds` with padding, centred, never smaller than `min` on either side. */
export function fitView(bounds: Box, pad = 28, min = 260): Box {
  const w = Math.max(bounds.w + pad * 2, min);
  const h = Math.max(bounds.h + pad * 2, min);
  return { x: bounds.x + bounds.w / 2 - w / 2, y: bounds.y + bounds.h / 2 - h / 2, w, h };
}

const ACTIVE_JOB: readonly Job["status"][] = ["queued", "planning", "planned", "running"];

/**
 * The offline agent queues this job is waiting on: the queue of every task that still has to run, plus the
 * planner while the plan does not exist yet. Empty when the job is finished or nothing it needs is offline.
 * `offline` comes from the dashboard summary (worker heartbeats).
 */
export function blockedByOfflineQueues(job: Job, tasks: readonly Task[], offline: readonly string[] | undefined): string[] {
  if (!offline?.length || !ACTIVE_JOB.includes(job.status)) return [];
  const needed = new Set<string>();
  const waiting = tasks.filter((t) => t.status === "pending" || t.status === "queued");
  for (const t of waiting) needed.add(t.agent);
  if (!tasks.some((t) => t.agent !== "planner") && (job.status === "queued" || job.status === "planning")) needed.add("planner");
  return [...needed].filter((q) => offline.includes(q));
}

/** Seconds a task ran (or has been running), from real timestamps; null when it has not started. */
export function taskSeconds(task: Pick<Task, "started_at" | "finished_at">, now: number): number | null {
  if (!task.started_at) return null;
  const end = task.finished_at ? Date.parse(task.finished_at) : now;
  return Math.max(0, Math.round((end - Date.parse(task.started_at)) / 1000));
}

/** Sum of the costs the API reports; null when none of the tasks has an actual cost yet. */
export function actualCost(tasks: readonly Task[]): number | null {
  const known = tasks.map((t) => t.actual_cost_usd).filter((c): c is number => c !== null && c !== undefined);
  return known.length ? known.reduce((a, b) => a + b, 0) : null;
}
