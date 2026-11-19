"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AgentGraph } from "@/components/AgentGraph";
import { ApprovalPanel } from "@/components/ApprovalPanel";
import { ArtifactGrid } from "@/components/artifacts";
import { JobStatusBadge, TaskStatusBadge, canDeleteJob, useDeleteJob } from "@/components/status";
import {
  Badge,
  Button,
  Card,
  DemoBadge,
  EmptyState,
  ErrorState,
  LoadingBlock,
  ProgressBar,
  RefreshError,
  SectionTitle,
  buttonClass,
  cx,
  type Tone,
} from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError, api, stabilizeArtifacts, toApiError } from "@/lib/api";
import {
  AGENTS,
  TERMINAL_JOB_STATUSES,
  type Artifact,
  type ItemList,
  type Job,
  type JobDetail,
  type JobEvent,
  type QaReport,
  type QaVerdict,
  type Task,
} from "@/lib/types";
import { useApi } from "@/lib/useApi";

const POLL_MS = 3000;
// After the job is terminal nothing changes except the signed download URLs,
// which expire (15 min by default); re-list artifacts well inside that window.
const ARTIFACT_URL_REFRESH_MS = 4 * 60 * 1000;

interface MonitorData {
  job: JobDetail;
  tasks: Task[];
  events: JobEvent[];
  qa: QaReport | null;
  qaError: ApiError | null;
}

const VERDICT_TONES: Record<QaVerdict, Tone> = { PASS: "success", REVISE: "warn", HUMAN_REVIEW: "info" };
const CHECK_TONES: Record<QaReport["checks"][number]["status"], Tone> = { pass: "success", warn: "warn", fail: "danger" };
const FINISHED_TASK: readonly string[] = ["succeeded", "failed", "skipped", "cancelled"];

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-dim">{label}</dt>
      <dd className="mt-0.5 text-sm text-ink">{children}</dd>
    </div>
  );
}

function TaskCard({ task, byKey }: { task: Task; byKey: Map<string, Task> }) {
  const { t, tx, money } = useI18n();
  const retried = task.attempt > 1;
  return (
    <li
      className={cx(
        "rounded-xl border bg-bg p-3",
        task.status === "failed" ? "border-bad/50" : "border-line",
      )}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-ink">{task.title}</p>
          <p className="font-mono text-xs text-dim">{task.key}</p>
        </div>
        <TaskStatusBadge status={task.status} />
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {task.provider && (
          <Badge title={t("monitor.provider")}>
            <span className="sr-only">{t("monitor.provider")}: </span>
            {task.provider}
          </Badge>
        )}
        {task.provider === "demo" && <DemoBadge />}
        {task.execution_location && (
          <Badge tone={task.execution_location === "cloud" ? "info" : "success"} title={t("monitor.location")}>
            <span className="sr-only">{t("monitor.location")}: </span>
            {tx("monitor.loc", task.execution_location)}
          </Badge>
        )}
        <span className="text-xs tabular-nums text-dim">
          {t("monitor.attempt", { n: task.attempt, max: task.max_attempts })}
        </span>
        {retried && (
          <Badge tone="warn">
            <span aria-hidden="true">↻</span>
            {t("monitor.retry")}
          </Badge>
        )}
        {(task.actual_cost_usd !== null || task.estimated_cost_usd !== null) && (
          <span className="ml-auto text-xs tabular-nums text-dim">
            {money(task.actual_cost_usd ?? task.estimated_cost_usd)}
          </span>
        )}
      </div>

      {task.routing_reason && (
        <p className="mt-2 text-xs text-dim">
          <span className="text-ink/80">{t("monitor.routing")}:</span> {task.routing_reason}
        </p>
      )}

      {task.depends_on.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-dim">
          <span>{t("monitor.dependsOn")}:</span>
          {task.depends_on.map((key) => {
            const dep = byKey.get(key);
            return (
              <span key={key} className="inline-flex items-center gap-1 rounded-md border border-line bg-raised px-1.5 py-0.5">
                <span className="font-mono">{key}</span>
                {dep && <span className="text-ink/80">· {tx("taskStatus", dep.status)}</span>}
              </span>
            );
          })}
        </div>
      )}

      {(task.error_code || task.error_message) && (
        <div role={task.status === "failed" ? "alert" : undefined} className="mt-3 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm">
          {task.error_code && <p className="font-mono text-xs text-bad">{task.error_code}</p>}
          {task.error_message && <p className="text-ink">{task.error_message}</p>}
        </div>
      )}
    </li>
  );
}

export function JobMonitor() {
  const { t, tx, money, dateTime, time } = useI18n();
  const { id } = useParams<{ id: string }>();
  const base = `/v1/jobs/${encodeURIComponent(id)}`;

  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  // Polling stops once the server reports a terminal status. Kept in state (not derived
  // from data inside the hook call) so the interval is known before data arrives.
  const [terminal, setTerminal] = useState(false);

  const monitor = useApi<MonitorData>(
    `job:${id}`,
    async (signal) => {
      const job = await api<JobDetail>(base, { signal });
      const isTerminal = TERMINAL_JOB_STATUSES.includes(job.status);
      const wantQa = job.qa_verdict !== null || isTerminal || job.status === "awaiting_approval";
      const [tasks, events, qaResult] = await Promise.all([
        api<ItemList<Task>>(`${base}/tasks`, { signal }),
        api<ItemList<JobEvent>>(`${base}/events`, { signal }),
        wantQa
          ? api<QaReport>(`${base}/qa`, { signal }).then(
              (qa) => ({ qa, qaError: null }),
              (e: unknown) => {
                if (signal.aborted) throw e;
                return { qa: null, qaError: toApiError(e) };
              },
            )
          : Promise.resolve({ qa: null, qaError: null }),
      ]);
      setTerminal(isTerminal);
      return { job, tasks: tasks.items, events: events.items, ...qaResult };
    },
    { interval: terminal ? 0 : POLL_MS },
  );

  const artifacts = useApi<Artifact[]>(
    `job-artifacts:${id}`,
    async (signal) => stabilizeArtifacts((await api<ItemList<Artifact>>(`${base}/artifacts`, { signal })).items),
    { interval: terminal ? ARTIFACT_URL_REFRESH_MS : POLL_MS },
  );

  const router = useRouter();
  const del = useDeleteJob(() => router.push("/jobs"));

  const reloadAll = () => {
    monitor.reload();
    artifacts.reload();
  };

  const cancel = async () => {
    if (!window.confirm(t("monitor.cancelConfirm"))) return;
    setCancelling(true);
    setActionError(null);
    try {
      await api<Job>(`${base}/cancel`, { method: "POST" });
    } catch (e) {
      setActionError(toApiError(e));
    }
    setCancelling(false);
    reloadAll();
  };

  const backLink = (
    <Link href="/jobs" className="mb-4 inline-flex items-center gap-1 text-sm text-dim hover:text-ink">
      <span aria-hidden="true">←</span> {t("monitor.back")}
    </Link>
  );

  if (!monitor.data) {
    return (
      <>
        {backLink}
        <h1 className="mb-6 text-2xl font-semibold tracking-tight text-ink">{t("monitor.title")}</h1>
        {monitor.loading && <LoadingBlock />}
        {monitor.error && <ErrorState error={monitor.error} onRetry={monitor.reload} />}
      </>
    );
  }

  const { job, tasks, events, qa, qaError } = monitor.data;
  const isTerminal = TERMINAL_JOB_STATUSES.includes(job.status);
  const byKey = new Map(tasks.map((task) => [task.key, task]));
  const groups = [
    ...AGENTS.map((agent) => ({ agent: agent as string, items: tasks.filter((task) => task.agent === agent) })),
    // Anything the contract doesn't list still gets shown rather than dropped.
    ...[...new Set(tasks.map((task) => task.agent as string))]
      .filter((a) => !(AGENTS as readonly string[]).includes(a))
      .map((agent) => ({ agent, items: tasks.filter((task) => task.agent === agent) })),
  ].filter((g) => g.items.length > 0);
  const doneTasks = tasks.filter((task) => FINISHED_TASK.includes(task.status)).length;
  const verdict = qa?.verdict ?? job.qa_verdict;
  const qaSummary = qa?.summary ?? job.qa_summary;

  return (
    <>
      {backLink}

      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">{t("monitor.title")}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <JobStatusBadge status={job.status} />
            {job.demo_mode && <DemoBadge />}
            {!isTerminal && (
              <span className="text-xs text-dim" role="status">
                {t("monitor.live")}
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <a href="#deliverables" className={buttonClass("secondary")}>
            {t("monitor.goToDeliverables")}
          </a>
          {!isTerminal && (
            <Button variant="danger" loading={cancelling} disabled={job.status === "cancelling"} onClick={cancel}>
              {t("monitor.cancel")}
            </Button>
          )}
          {canDeleteJob(job) && (
            <Button variant="danger" loading={del.deleting} onClick={() => del.remove(job)}>
              {t("jobs.delete")}
            </Button>
          )}
        </div>
      </div>

      {monitor.error && <RefreshError error={monitor.error} onRetry={monitor.reload} />}
      {actionError && (
        <div className="mb-4">
          <ErrorState error={actionError} compact />
        </div>
      )}
      {job.demo_mode && (
        <p className="mb-4 rounded-xl border border-warn/50 bg-warn/10 px-3 py-2 text-sm text-ink">{t("demo.jobNotice")}</p>
      )}
      {(job.error_code || job.error_message) && (
        <div role="alert" className="mb-4 rounded-xl border border-bad/40 bg-bad/10 px-4 py-3 text-sm">
          <p className="font-semibold text-bad">{t("monitor.jobError")}</p>
          {job.error_code && <p className="font-mono text-xs text-bad">{job.error_code}</p>}
          {job.error_message && <p className="mt-1 text-ink">{job.error_message}</p>}
        </div>
      )}

      {/* Full width so the wide graph layout stays legible on desktop. */}
      <Card className="mb-4">
        <SectionTitle>{t("graph.title")}</SectionTitle>
        <AgentGraph job={job} tasks={tasks} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <SectionTitle aside={<span className="text-sm font-semibold tabular-nums text-ink">{Math.round(job.progress)}%</span>}>
              {t("monitor.progress")}
            </SectionTitle>
            <ProgressBar value={job.progress} label={t("monitor.progress")} tone={job.status === "failed" ? "bad" : "brand"} />
            {tasks.length > 0 && (
              <p className="mt-2 text-xs text-dim">{t("monitor.tasksSummary", { done: doneTasks, total: tasks.length })}</p>
            )}
            <h3 className="mt-5 text-xs font-medium uppercase tracking-wide text-dim">{t("monitor.command")}</h3>
            <p className="mt-1 whitespace-pre-wrap text-sm text-ink">{job.command}</p>
          </Card>

          {job.status === "awaiting_approval" && <ApprovalPanel jobId={job.id} onDone={reloadAll} />}

          <Card>
            <SectionTitle>{t("monitor.tasks")}</SectionTitle>
            {groups.length === 0 ? (
              <EmptyState title={t("monitor.tasksEmpty")} hint={isTerminal ? undefined : t("monitor.tasksEmptyHint")} />
            ) : (
              <div className="space-y-5">
                {groups.map((group) => (
                  <section key={group.agent} aria-label={tx("agent", group.agent)}>
                    <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-ink">
                      <span aria-hidden="true" className="h-4 w-1 rounded-full bg-gradient-to-b from-brand to-cyan" />
                      {tx("agent", group.agent)}
                      <span className="font-normal text-dim">({group.items.length})</span>
                    </h3>
                    <ul className="grid gap-3 md:grid-cols-2">
                      {group.items.map((task) => (
                        <TaskCard key={task.id} task={task} byKey={byKey} />
                      ))}
                    </ul>
                  </section>
                ))}
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <SectionTitle>{t("monitor.cost")}</SectionTitle>
            <dl className="grid grid-cols-3 gap-3">
              <Fact label={t("monitor.cost.estimated")}>
                <span className="tabular-nums">{money(job.estimated_cost_usd)}</span>
              </Fact>
              <Fact label={t("monitor.cost.actual")}>
                <span className="tabular-nums">{money(job.actual_cost_usd)}</span>
              </Fact>
              <Fact label={t("monitor.cost.budget")}>
                <span className="tabular-nums">{money(job.budget_limit_usd)}</span>
              </Fact>
            </dl>
            {job.budget_limit_usd !== null && job.budget_limit_usd > 0 && (
              <div className="mt-3">
                <ProgressBar
                  value={((job.actual_cost_usd ?? 0) / job.budget_limit_usd) * 100}
                  label={t("monitor.cost")}
                  tone={(job.actual_cost_usd ?? 0) > job.budget_limit_usd ? "bad" : "brand"}
                />
              </div>
            )}
          </Card>

          <Card>
            <SectionTitle>{t("monitor.qa")}</SectionTitle>
            {verdict ? (
              <Badge tone={VERDICT_TONES[verdict] ?? "neutral"}>{tx("monitor.verdict", verdict)}</Badge>
            ) : (
              <p className="text-sm text-dim">{t("monitor.qaNotRun")}</p>
            )}
            {qaSummary && <p className="mt-3 whitespace-pre-wrap text-sm text-ink">{qaSummary}</p>}
            {qaError && (
              <div className="mt-3">
                <ErrorState error={qaError} compact />
              </div>
            )}
            {qa && qa.checks.length > 0 && (
              <>
                <h3 className="mt-4 text-xs font-medium uppercase tracking-wide text-dim">{t("monitor.qaChecks")}</h3>
                <ul className="mt-2 space-y-2">
                  {qa.checks.map((check, i) => (
                    <li key={`${check.name}-${i}`} className="rounded-lg border border-line bg-bg p-2.5">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm font-medium text-ink">{check.name}</span>
                        <Badge tone={CHECK_TONES[check.status] ?? "neutral"}>{tx("monitor.check", check.status)}</Badge>
                      </div>
                      {check.detail && <p className="mt-1 text-xs text-dim">{check.detail}</p>}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Card>

          <Card>
            <SectionTitle>{t("monitor.details")}</SectionTitle>
            <dl className="grid grid-cols-2 gap-3">
              <Fact label={t("monitor.language")}>{tx("lang", job.language)}</Fact>
              <Fact label={t("monitor.aspect")}>{job.aspect_ratio}</Fact>
              <Fact label={t("monitor.classification")}>{tx("class", job.data_classification)}</Fact>
              <Fact label={t("monitor.planner")}>{tx("monitor.plannerMode", job.planner_mode)}</Fact>
              <Fact label={t("monitor.approval")}>
                {job.approval_status
                  ? tx("monitor.approvalStatus", job.approval_status)
                  : job.require_approval
                    ? t("monitor.approvalRequired")
                    : t("monitor.approvalNotRequired")}
              </Fact>
              <Fact label={t("workforce.agents")}>
                {job.requested_agents.length > 0 ? job.requested_agents.map((a) => tx("agent", a)).join(", ") : "—"}
              </Fact>
              <Fact label={t("monitor.created")}>{dateTime(job.created_at)}</Fact>
              <Fact label={t("monitor.started")}>{dateTime(job.started_at)}</Fact>
              <Fact label={t("monitor.finished")}>{dateTime(job.finished_at)}</Fact>
            </dl>
            {job.plan && (
              <details className="mt-4">
                <summary className="cursor-pointer text-sm text-brand-soft">{t("monitor.plan")}</summary>
                <pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-line bg-bg p-3 text-xs text-dim">
                  {JSON.stringify(job.plan, null, 2)}
                </pre>
              </details>
            )}
          </Card>

          <Card>
            <SectionTitle>{t("monitor.timeline")}</SectionTitle>
            {events.length === 0 ? (
              <p className="text-sm text-dim">{t("monitor.timelineEmpty")}</p>
            ) : (
              <ol className="max-h-[28rem] space-y-3 overflow-y-auto pr-1">
                {events.map((event) => (
                  <li key={event.id} className="relative border-l border-line pl-4">
                    <span aria-hidden="true" className="absolute -left-[5px] top-1.5 size-2.5 rounded-full bg-brand" />
                    <p className="font-mono text-xs text-ink">{event.action}</p>
                    <p className="text-xs text-dim">
                      <time dateTime={event.at} title={dateTime(event.at)}>
                        {time(event.at)}
                      </time>
                      {" · "}
                      {event.actor}
                      {event.task_key && (
                        <>
                          {" · "}
                          <span className="font-mono">{event.task_key}</span>
                        </>
                      )}
                    </p>
                    {event.detail && Object.keys(event.detail).length > 0 && (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-xs text-brand-soft">{t("monitor.eventDetail")}</summary>
                        <pre className="mt-1 max-h-40 overflow-auto rounded-md border border-line bg-bg p-2 text-[11px] text-dim">
                          {JSON.stringify(event.detail, null, 2)}
                        </pre>
                      </details>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </div>

      <section id="deliverables" className="mt-8 scroll-mt-20">
        <ScrollHereOnHash hash="#deliverables" />
        <SectionTitle>{t("monitor.deliverables")}</SectionTitle>
        {artifacts.loading && <LoadingBlock />}
        {artifacts.error && !artifacts.data && <ErrorState error={artifacts.error} onRetry={artifacts.reload} />}
        {artifacts.error && artifacts.data && <RefreshError error={artifacts.error} onRetry={artifacts.reload} />}
        {artifacts.data &&
          (artifacts.data.length === 0 ? (
            <EmptyState
              title={t("monitor.deliverablesEmpty")}
              hint={isTerminal ? undefined : t("monitor.deliverablesEmptyRunning")}
            />
          ) : (
            <ArtifactGrid artifacts={artifacts.data} onChanged={reloadAll} />
          ))}
      </section>
    </>
  );
}

/** Scrolls its parent section into view once, when the page was opened with the given URL hash. */
function ScrollHereOnHash({ hash }: { hash: string }) {
  const marker = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (window.location.hash !== hash) return;
    // wait a frame so the cards above have their final height
    const id = requestAnimationFrame(() => marker.current?.parentElement?.scrollIntoView({ block: "start" }));
    return () => cancelAnimationFrame(id);
  }, [hash]);
  return <span ref={marker} hidden />;
}
