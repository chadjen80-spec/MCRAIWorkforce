"use client";

import Link from "next/link";
import { useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";
import { api, toApiError } from "@/lib/api";
import type { Job, JobStatus, TaskStatus } from "@/lib/types";
import { Badge, DemoBadge, ProgressBar, type Tone } from "./ui";

const JOB_TONES: Record<JobStatus, Tone> = {
  queued: "neutral",
  planning: "info",
  planned: "info",
  running: "info",
  awaiting_approval: "warn",
  completed: "success",
  partial: "warn",
  failed: "danger",
  cancelling: "neutral",
  cancelled: "neutral",
  rejected: "danger",
};

const TASK_TONES: Record<TaskStatus, Tone> = {
  pending: "neutral",
  queued: "neutral",
  running: "info",
  succeeded: "success",
  failed: "danger",
  skipped: "neutral",
  cancelled: "neutral",
};

const LIVE: readonly string[] = ["planning", "running", "cancelling"];

function Dot({ live }: { live: boolean }) {
  return <span aria-hidden="true" className={`size-1.5 rounded-full bg-current ${live ? "animate-pulse" : ""}`} />;
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const { tx } = useI18n();
  return (
    <Badge tone={JOB_TONES[status] ?? "neutral"}>
      <Dot live={LIVE.includes(status)} />
      {tx("status", status)}
    </Badge>
  );
}

export function TaskStatusBadge({ status }: { status: TaskStatus }) {
  const { tx } = useI18n();
  return (
    <Badge tone={TASK_TONES[status] ?? "neutral"}>
      <Dot live={status === "running"} />
      {tx("taskStatus", status)}
    </Badge>
  );
}

/** Jobs that are still being worked on must be cancelled before they can be deleted. */
const ACTIVE: readonly JobStatus[] = ["queued", "planning", "planned", "running", "cancelling"];

export function canDeleteJob(job: Job): boolean {
  return !ACTIVE.includes(job.status);
}

/** Confirms, then permanently deletes a job. Returns an error message, or null on success/abort. */
export function useDeleteJob(onDeleted: () => void) {
  const { t } = useI18n();
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remove = async (job: Job) => {
    if (!window.confirm(t("jobs.deleteConfirm"))) return;
    setDeleting(true);
    setError(null);
    try {
      await api<void>(`/v1/jobs/${job.id}`, { method: "DELETE" });
      onDeleted();
    } catch (e) {
      setError(t("jobs.deleteFailed", { message: toApiError(e).message }));
    }
    setDeleting(false);
  };
  return { deleting, error, remove };
}

/** One job in a list; the whole row links to the Job Monitor. Pass onDeleted to offer a Delete button. */
export function JobRow({ job, onDeleted }: { job: Job; onDeleted?: () => void }) {
  const { t, money, dateTime } = useI18n();
  const cost = job.actual_cost_usd ?? job.estimated_cost_usd;
  const del = useDeleteJob(onDeleted ?? (() => {}));
  const deletable = onDeleted !== undefined && canDeleteJob(job);
  return (
    <li className="relative">
      {deletable && (
        <button
          type="button"
          onClick={() => del.remove(job)}
          disabled={del.deleting}
          className="absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-lg border border-bad/40 bg-bad/10 px-2.5 py-1 text-xs font-medium text-bad transition hover:bg-bad/20 disabled:opacity-60"
        >
          <svg aria-hidden="true" viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
          </svg>
          {del.deleting ? t("jobs.deleting") : t("jobs.delete")}
        </button>
      )}
      <Link
        href={`/jobs/${job.id}`}
        className="block rounded-xl border border-line bg-surface p-4 transition hover:border-brand-soft/60 hover:bg-raised"
      >
        <div className={deletable ? "flex flex-wrap items-center gap-2 pr-20" : "flex flex-wrap items-center gap-2"}>
          <JobStatusBadge status={job.status} />
          {job.demo_mode && <DemoBadge />}
          <span className="ml-auto text-xs text-dim">{dateTime(job.created_at)}</span>
        </div>
        <p className="mt-2 line-clamp-2 text-sm text-ink">{job.command}</p>
        {del.error && (
          <p role="alert" className="mt-2 text-xs text-bad">
            {del.error}
          </p>
        )}
        <div className="mt-3 flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <ProgressBar value={job.progress} label={t("jobs.progress")} tone={job.status === "failed" ? "bad" : "brand"} />
          </div>
          <span className="w-10 text-right text-xs tabular-nums text-dim">{Math.round(job.progress)}%</span>
          <span className="text-xs tabular-nums text-dim">
            {t("jobs.cost")}: <span className="text-ink">{money(cost)}</span>
          </span>
        </div>
      </Link>
    </li>
  );
}
