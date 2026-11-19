// The workflows Mira can actually run today, and what she suggests next.
//
// Every quick action here maps to a deliverable the planner supports (see apps/api/app/agents/planner.py):
// images, social captions, documents (proposal / quotation / campaign brief / marketing report as PDF or DOCX),
// short video, and posters designed on Mira's screen. Business features that do not exist yet (the rest of
// Quote-to-Cash) are listed separately as PLANNED and are never rendered as working actions.

import type { MessageKey } from "@/i18n/dictionaries";
import type { DashboardSummary } from "./types";

/** Agent queues as reported by the API's worker heartbeats (`offline_queues`). */
export type AgentQueue = "planner" | "content" | "image" | "document" | "video" | "qa";

export type WorkflowId = "campaign" | "captions" | "poster" | "video" | "quotation" | "brief";

export interface Workflow {
  id: WorkflowId;
  /** "command" fills the composer for you to finish and confirm; "poster" opens the free poster design. */
  kind: "command" | "poster";
  /** Queues the job needs (besides planner and QA, which every job uses). */
  agents: AgentQueue[];
  icon: "images" | "captions" | "poster" | "video" | "quote" | "doc";
  title: MessageKey;
  description: MessageKey;
  /** The command text placed in the composer. Ends where you add what it is for. */
  command: MessageKey;
}

export const WORKFLOWS: readonly Workflow[] = [
  { id: "campaign", kind: "command", agents: ["image", "content"], icon: "images", title: "qa.campaign.title", description: "qa.campaign.desc", command: "qa.campaign.command" },
  { id: "captions", kind: "command", agents: ["content"], icon: "captions", title: "qa.captions.title", description: "qa.captions.desc", command: "qa.captions.command" },
  { id: "poster", kind: "poster", agents: ["image"], icon: "poster", title: "qa.poster.title", description: "qa.poster.desc", command: "qa.poster.command" },
  { id: "video", kind: "command", agents: ["video", "content"], icon: "video", title: "qa.video.title", description: "qa.video.desc", command: "qa.video.command" },
  { id: "quotation", kind: "command", agents: ["document"], icon: "quote", title: "qa.quotation.title", description: "qa.quotation.desc", command: "qa.quotation.command" },
  { id: "brief", kind: "command", agents: ["document"], icon: "doc", title: "qa.brief.title", description: "qa.brief.desc", command: "qa.brief.command" },
];

/** Quote-to-Cash steps that are not built yet. Shown as "Planned" text only, never as buttons. */
export const PLANNED: readonly { id: string; title: MessageKey }[] = [
  { id: "invoice", title: "planned.invoice" },
  { id: "payment", title: "planned.payment" },
  { id: "followup", title: "planned.followup" },
];

const SHARED: AgentQueue[] = ["planner", "qa"];

/** Queues this workflow needs that have no live worker right now (the job would wait in line). */
export function offlineFor(workflow: Workflow, offline: readonly string[] | undefined): AgentQueue[] {
  if (!offline?.length) return [];
  return [...SHARED, ...workflow.agents].filter((q) => offline.includes(q));
}

export function workflowById(id: WorkflowId): Workflow {
  const w = WORKFLOWS.find((x) => x.id === id);
  if (!w) throw new Error(`unknown workflow ${id}`);
  return w;
}

export interface Suggestion {
  id: string;
  tone: "action" | "warn" | "info";
  label: MessageKey;
  vars?: Record<string, string>;
  /** A page to open... */
  href?: string;
  /** ...or a quick action to start. A suggestion has at most one of the two. */
  workflow?: WorkflowId;
}

/** True when the organisation has never run a job (nothing to review, nothing in history). */
export function isFirstRun(s: DashboardSummary | null | undefined): boolean {
  if (!s) return false;
  return s.active_jobs + s.completed_jobs + s.failed_jobs + s.awaiting_approval_jobs === 0 && s.recent_artifacts.length === 0;
}

/**
 * What Mira suggests next, most urgent first: things waiting on you, then problems, then a useful next
 * job based on what the team has produced and which agents are online. At most `max` items.
 */
export function suggestTasks(s: DashboardSummary | null | undefined, max = 4): Suggestion[] {
  if (!s) return [];
  const out: Suggestion[] = [];
  const offline = s.offline_queues ?? [];
  const runnable = (id: WorkflowId) => offlineFor(workflowById(id), offline).length === 0;

  if (s.awaiting_approval_jobs > 0)
    out.push({ id: "review", tone: "action", label: "suggest.review", vars: { n: String(s.awaiting_approval_jobs) }, href: "/jobs?status=awaiting_approval" });
  if (s.failed_jobs > 0)
    out.push({ id: "failed", tone: "warn", label: "suggest.failed", vars: { n: String(s.failed_jobs) }, href: "/jobs?status=failed" });
  if (offline.length > 0) out.push({ id: "offline", tone: "warn", label: "suggest.offline", vars: { queues: offline.join(", ") } });
  if (s.monthly_budget_usd > 0 && s.month_cost_usd / s.monthly_budget_usd >= 0.8)
    out.push({
      id: "budget",
      tone: "warn",
      label: "suggest.budget",
      vars: { pct: String(Math.min(100, Math.round((s.month_cost_usd / s.monthly_budget_usd) * 100))) },
      href: "/dashboard",
    });

  // A next job that builds on what exists, only when the agents it needs are online.
  const kinds = new Set(s.recent_artifacts.map((a) => a.kind));
  const ideas: [WorkflowId, MessageKey][] = isFirstRun(s)
    ? [["campaign", "suggest.firstJob"]]
    : [
        ...(kinds.has("image") && !kinds.has("caption") ?([["captions", "suggest.captionsForImages"]] as [WorkflowId, MessageKey][]) : []),
        ...(kinds.has("image") && !kinds.has("video") ? ([["video", "suggest.videoFromCampaign"]] as [WorkflowId, MessageKey][]) : []),
        ["quotation", "suggest.quotation"],
      ];
  for (const [workflow, label] of ideas) if (runnable(workflow)) out.push({ id: `wf-${workflow}`, tone: "info", label, workflow });

  return out.slice(0, max);
}
