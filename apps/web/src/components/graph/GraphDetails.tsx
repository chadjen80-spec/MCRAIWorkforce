"use client";

import { TaskStatusBadge } from "@/components/status";
import { cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { fileUrl } from "@/lib/api";
import {
  actualCost,
  aiProgress,
  approvalState,
  hubState,
  isRetrying,
  taskSeconds,
  type GraphModel,
  type HubAgent,
  type NodeState,
} from "@/lib/agentGraph";
import type { Artifact, Job, QaReport, Task } from "@/lib/types";

export type Selection = { kind: "master" } | { kind: "agent"; agent: HubAgent } | { kind: "task"; taskId: string };

export const STATE_DOT: Record<NodeState, string> = {
  idle: "#a8b6d0",
  queued: "#fdba74",
  working: "#fde68a",
  retrying: "#fb923c",
  done: "#6ee7b7",
  failed: "#fca5a5",
  off: "#64748b",
};

/** "2 min 5 s" in the page language. */
export function formatSeconds(total: number, locale: string): string {
  const tag = locale === "th" ? "th-TH" : "en-US";
  const unit = (n: number, u: "hour" | "minute" | "second") => new Intl.NumberFormat(tag, { style: "unit", unit: u, unitDisplay: "long" }).format(n);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${unit(h, "hour")} ${unit(m, "minute")}`;
  if (m) return `${unit(m, "minute")} ${unit(s, "second")}`;
  return unit(s, "second");
}

/** AI processing and human approval, side by side and never mixed. */
export function ProgressStrip({ job, tasks }: { job: Job; tasks: readonly Task[] }) {
  const { t } = useI18n();
  const ai = aiProgress(job, tasks);
  const approval = approvalState(job);
  const approvalTone =
    approval === "waiting" ? "border-warn/50 text-warn" : approval === "approved" ? "border-ok/50 text-ok" : approval === "rejected" ? "border-bad/50 text-bad" : "border-line text-dim";
  return (
    <div className="mb-2 grid gap-2 sm:grid-cols-2">
      <div className="rounded-lg border border-line bg-[#0b1322] px-3 py-2">
        <p className="text-xs font-semibold text-dim">{t("graph.ai.title")}</p>
        <div className="mt-1 flex items-center gap-2">
          <div
            role="progressbar"
            aria-label={t("graph.ai.title")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={ai.pct}
            className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-raised"
          >
            <div className={cx("h-full rounded-full", ai.failed ? "bg-bad" : "bg-gradient-to-r from-brand to-cyan")} style={{ width: `${ai.pct}%` }} />
          </div>
          <span className="text-xs tabular-nums text-ink">{ai.pct}%</span>
        </div>
        <p className="mt-1 text-xs text-ink">
          {t("graph.ai.line", { done: String(ai.done), total: String(ai.total) })}
          {ai.failed > 0 && <span className="text-bad"> · {t("graph.ai.failed", { n: String(ai.failed) })}</span>}
          {ai.retrying > 0 && <span className="text-warn"> · {t("graph.ai.retrying", { n: String(ai.retrying) })}</span>}
        </p>
      </div>
      <div className={cx("rounded-lg border bg-[#0b1322] px-3 py-2", approvalTone)}>
        <p className="text-xs font-semibold text-dim">{t("graph.approval.title")}</p>
        <p className="mt-1 text-sm font-medium">{t(`graph.approval.${approval}`)}</p>
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap gap-x-2 text-xs">
      <dt className="text-dim">{label}</dt>
      <dd className="min-w-0 text-ink">{children}</dd>
    </div>
  );
}

function TaskDetails({ task, tasks, artifacts, now }: { task: Task; tasks: readonly Task[]; artifacts?: Artifact[]; now: number }) {
  const { t, tx, money, locale } = useI18n();
  const secs = taskSeconds(task, now);
  const deps = task.depends_on.map((k) => tasks.find((x) => x.key === k)).filter((x): x is Task => Boolean(x));
  const files = artifacts?.filter((a) => a.task_id === task.id);
  return (
    <li className={cx("rounded-lg border bg-[#0b1322] p-2.5", task.status === "failed" ? "border-bad/60" : isRetrying(task) ? "border-warn/60" : "border-line")}>
      <div className="flex flex-wrap items-center gap-2">
        <TaskStatusBadge status={task.status} />
        <span className="text-sm font-medium text-ink">{task.title || task.key}</span>
        {isRetrying(task) && <span className="text-xs font-semibold text-warn">{t("graph.details.retrying", { n: String(task.attempt), max: String(task.max_attempts) })}</span>}
        {!isRetrying(task) && task.attempt > 1 && <span className="text-xs text-warn">{t("graph.attempt", { n: task.attempt, max: task.max_attempts })}</span>}
      </div>
      <dl className="mt-1.5 space-y-0.5">
        <Row label={t("graph.details.time")}>
          {secs === null ? t("graph.details.notStarted") : task.finished_at ? formatSeconds(secs, locale) : t("graph.details.runningFor", { time: formatSeconds(secs, locale) })}
        </Row>
        <Row label={t("graph.details.cost")}>
          {task.actual_cost_usd !== null && task.actual_cost_usd !== undefined ? money(task.actual_cost_usd) : t("graph.details.costUnknown")}
          {task.actual_cost_usd == null && task.estimated_cost_usd != null && <span className="text-dim"> · {t("graph.details.estimate", { cost: money(task.estimated_cost_usd) })}</span>}
        </Row>
        {task.provider && (
          <Row label={t("graph.details.provider")}>
            {task.provider}
            {task.execution_location ? ` · ${tx("monitor.loc", task.execution_location)}` : ""}
          </Row>
        )}
        {task.routing_reason && <Row label={t("graph.details.why")}>{task.routing_reason}</Row>}
        {deps.length > 0 && (
          <Row label={t("graph.details.dependsOn")}>
            {deps.map((d, i) => (
              <span key={d.id}>
                {i > 0 && ", "}
                {d.title || d.key} ({tx("taskStatus", d.status)})
              </span>
            ))}
          </Row>
        )}
      </dl>
      {(task.error_code || task.error_message) && (
        <p role="note" className="mt-1.5 rounded-md border border-bad/40 bg-bad/10 px-2 py-1 text-xs text-ink">
          {task.error_code && <span className="font-mono text-bad">{task.error_code}</span>}
          {task.error_code && task.error_message && " · "}
          {task.error_message}
        </p>
      )}
      {files && files.length > 0 && (
        <ul className="mt-1.5 flex flex-wrap gap-1.5">
          {files.map((a) => (
            <li key={a.id}>
              {a.download_url ? (
                <a href={fileUrl(a.download_url)} target="_blank" rel="noreferrer" className="inline-block rounded-md border border-line px-2 py-0.5 text-xs text-brand-soft hover:border-brand-soft/60">
                  {a.filename}
                </a>
              ) : (
                <span className="inline-block rounded-md border border-line px-2 py-0.5 text-xs text-dim">{a.filename}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function QaSection({ job, qa }: { job: Job; qa?: QaReport | null }) {
  const { t } = useI18n();
  const verdict = qa?.verdict ?? job.qa_verdict;
  const summary = qa?.summary ?? job.qa_summary;
  if (!verdict) return <p className="text-xs text-dim">{t("graph.qa.pending")}</p>;
  const tone = verdict === "PASS" ? "border-ok/50 text-ok" : verdict === "REVISE" ? "border-warn/50 text-warn" : "border-bad/50 text-bad";
  return (
    <div className={cx("rounded-lg border bg-[#0b1322] p-2.5", tone)}>
      <p className="text-sm font-semibold">
        {t("graph.qa.title")}: {verdict}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-ink">{t(`graph.qa.${verdict}`)}</p>
      {verdict === "PASS" && <p className="mt-1 text-xs text-ink">{t(job.require_approval ? "graph.qa.passApproval" : "graph.qa.passRelease")}</p>}
      {summary && <p className="mt-1 text-xs text-dim">{summary}</p>}
      {qa && qa.checks.length > 0 && (
        <ul className="mt-2 space-y-1" aria-label={t("graph.qa.checks")}>
          {qa.checks.map((c, i) => (
            <li key={`${c.name}-${i}`} className="flex gap-2 text-xs">
              <span className={cx("shrink-0 font-semibold uppercase", c.status === "pass" ? "text-ok" : c.status === "warn" ? "text-warn" : "text-bad")}>{c.status}</span>
              <span className="text-ink">
                {c.name}
                {c.detail && <span className="text-dim"> · {c.detail}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** What the selected node is doing, from the job, task, QA and artifact responses. */
export function GraphDetails({
  selection,
  job,
  tasks,
  model,
  qa,
  artifacts,
  now,
  onClose,
  onOpenFiles,
}: {
  selection: Selection;
  job: Job;
  tasks: readonly Task[];
  model: GraphModel;
  qa?: QaReport | null;
  artifacts?: Artifact[];
  now: number;
  onClose: () => void;
  onOpenFiles?: () => void;
}) {
  const { t, tx, money, locale } = useI18n();
  let title: string;
  let state: NodeState;
  let list: Task[];
  if (selection.kind === "master") {
    title = t("graph.master");
    list = model.planner ? [model.planner] : [];
    state = hubState(list);
  } else if (selection.kind === "agent") {
    title = tx("agent", selection.agent);
    list = model.byAgent.get(selection.agent) ?? [];
    state = hubState(list);
  } else {
    const task = tasks.find((x) => x.id === selection.taskId);
    title = task ? task.title || task.key : "";
    list = task ? [task] : [];
    state = hubState(list);
  }
  const cost = selection.kind === "master" ? (job.actual_cost_usd ?? null) : actualCost(list);
  const providers = [...new Set(list.map((x) => x.provider).filter(Boolean))];
  const jobSecs = taskSeconds(job, now);
  const showQa = (selection.kind === "agent" && selection.agent === "qa") || (selection.kind === "task" && list[0]?.agent === "qa");

  return (
    <section aria-labelledby="graph-details-title" className="rounded-xl border border-cyan/40 bg-raised/60 p-3">
      <div className="mb-2 flex items-start gap-2">
        <span aria-hidden="true" className="mt-1.5 size-2.5 shrink-0 rounded-full" style={{ background: STATE_DOT[state] }} />
        <div className="min-w-0 flex-1">
          <h3 id="graph-details-title" className="font-semibold text-ink">
            {title}
          </h3>
          <p className="text-xs text-dim">{selection.kind === "master" ? tx("status", job.status) : t(`graph.state.${state}`)}</p>
        </div>
        <button type="button" onClick={onClose} className="rounded-md px-2 py-1 text-xs text-dim hover:bg-raised hover:text-ink">
          {t("graph.details.close")}
        </button>
      </div>

      <dl className="mb-2 space-y-0.5">
        {selection.kind === "master" && jobSecs !== null && (
          <Row label={t("graph.details.time")}>{job.finished_at ? formatSeconds(jobSecs, locale) : t("graph.details.runningFor", { time: formatSeconds(jobSecs, locale) })}</Row>
        )}
        {list.length > 1 || selection.kind === "master" ? (
          <Row label={t("graph.details.cost")}>{cost !== null ? money(cost) : t("graph.details.costUnknown")}</Row>
        ) : null}
        {list.length > 1 && providers.length > 0 && <Row label={t("graph.details.provider")}>{providers.join(", ")}</Row>}
      </dl>

      {selection.kind === "master" && <ProgressStrip job={job} tasks={tasks} />}
      {selection.kind === "master" && (job.error_code || job.error_message) && (
        <p role="note" className="mb-2 rounded-md border border-bad/40 bg-bad/10 px-2 py-1 text-xs text-ink">
          {job.error_code && <span className="font-mono text-bad">{job.error_code}</span>}
          {job.error_code && job.error_message && " · "}
          {job.error_message}
        </p>
      )}
      {showQa && (
        <div className="mb-2">
          <QaSection job={job} qa={qa} />
        </div>
      )}

      {list.length === 0 ? (
        <p className="text-xs text-dim">{t("graph.idle")}</p>
      ) : (
        <ul className="space-y-2">
          {list.map((task) => (
            <TaskDetails key={task.id} task={task} tasks={tasks} artifacts={artifacts} now={now} />
          ))}
        </ul>
      )}
      {artifacts === undefined && selection.kind !== "master" && list.some((x) => x.status === "succeeded") && (
        <p className="mt-2 text-xs text-dim">{t("graph.details.outputsElsewhere")}</p>
      )}
      {showQa && onOpenFiles && (job.qa_verdict || qa?.verdict) && (
        <button type="button" onClick={onOpenFiles} className="mt-2 rounded-md border border-brand/60 px-3 py-1 text-xs font-semibold text-brand-soft hover:bg-brand/15">
          {t("graph.openFiles")} ›
        </button>
      )}
    </section>
  );
}
