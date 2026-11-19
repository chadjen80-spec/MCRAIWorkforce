"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { JobStatusBadge } from "@/components/status";
import { DemoBadge, ProgressBar, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { ApiError } from "@/lib/api";
import { budgetFigures } from "@/lib/briefing";
import type { DashboardSummary, Job } from "@/lib/types";
import { suggestTasks, type Suggestion, type WorkflowId } from "@/lib/workflows";

/** The current time, refreshed every minute (for "waiting since" labels). */
function useNow(every = 60000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), every);
    return () => clearInterval(id);
  }, [every]);
  return now;
}

/** "3 hours ago" in the page language, from real timestamps only. */
export function timeAgo(iso: string, now: number, locale: string): string {
  const secs = Math.round((new Date(iso).getTime() - now) / 1000);
  const rtf = new Intl.RelativeTimeFormat(locale === "th" ? "th-TH" : "en-US", { numeric: "auto" });
  const abs = Math.abs(secs);
  if (abs < 60) return rtf.format(secs, "second");
  if (abs < 3600) return rtf.format(Math.round(secs / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(secs / 3600), "hour");
  return rtf.format(Math.round(secs / 86400), "day");
}

/**
 * What needs the manager's attention right now: jobs waiting for approval (each with a direct Review link,
 * how long it has waited and its QA verdict), live work (with "Watch live"), failures, budget and offline
 * workers. Every number links to the list behind it. There is no deadline or business priority in the
 * data model, so none is shown.
 */
export function OpsSummary({
  summary,
  waiting,
  waitingTotal,
  live = [],
  loading = false,
  error,
  takenAt,
  onRetry,
  onWatch,
  watchingJobId = null,
  now: nowProp,
}: {
  summary: DashboardSummary | null | undefined;
  /** The newest jobs awaiting approval (a few). */
  waiting: Job[] | undefined;
  waitingTotal?: number;
  /** Jobs still being worked on. */
  live?: Job[];
  /** Nothing loaded yet. */
  loading?: boolean;
  /** The last refresh failed (summary may still hold the previous snapshot). */
  error?: ApiError;
  /** When the shown snapshot was taken (ms). */
  takenAt?: number;
  onRetry?: () => void;
  onWatch?: (jobId: string) => void;
  watchingJobId?: string | null;
  now?: number;
}) {
  const { t, money, locale, dateTime } = useI18n();
  const ticking = useNow();
  const now = nowProp ?? ticking;
  const s = summary;
  const budget = s ? budgetFigures(s) : null;
  const offline = s?.offline_queues ?? [];
  const total = waitingTotal ?? s?.awaiting_approval_jobs ?? 0;

  if (loading && !s)
    return (
      <section aria-labelledby="ops-title" aria-busy="true" className="rounded-2xl border border-line bg-surface p-4">
        <h2 id="ops-title" className="text-lg font-semibold text-ink">
          {t("ops.title")}
        </h2>
        <p role="status" className="mt-2 text-sm text-dim">
          {t("ops.loading")}
        </p>
        <div aria-hidden="true" className="mt-3 grid grid-cols-3 gap-2">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-16 animate-pulse rounded-xl bg-raised" />
          ))}
        </div>
      </section>
    );

  return (
    <section aria-labelledby="ops-title" className="rounded-2xl border border-line bg-surface p-4">
      <h2 id="ops-title" className="text-lg font-semibold text-ink">
        {t("ops.title")}
      </h2>
      {error && (
        <div role="alert" className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-bad/40 bg-bad/10 p-3 text-sm text-ink">
          <span className="min-w-0 flex-1">
            {s && takenAt ? t("ops.stale", { time: dateTime(new Date(takenAt).toISOString()) }) : t("ops.loadFailed")}
          </span>
          {onRetry && (
            <button type="button" onClick={onRetry} className="rounded-lg border border-line bg-raised px-3 py-1 font-medium text-ink hover:border-brand-soft/60">
              {t("ops.retry")}
            </button>
          )}
        </div>
      )}
      {!s ? null : (
      <>
      <ul className="mt-3 grid grid-cols-3 gap-2">
        <Stat
          label={t("ops.awaiting")}
          value={s?.awaiting_approval_jobs}
          href="/jobs?status=awaiting_approval"
          tone={s && s.awaiting_approval_jobs > 0 ? "warn" : "plain"}
        />
        <Stat label={t("ops.running")} value={s?.active_jobs} href="/jobs" tone="plain" />
        <Stat label={t("ops.failed")} value={s?.failed_jobs} href="/jobs?status=failed" tone={s && s.failed_jobs > 0 ? "bad" : "plain"} />
      </ul>

      {budget && (
        <Link href="/dashboard" className="mt-3 block rounded-xl border border-line bg-bg p-3 transition hover:border-brand-soft/60">
          <span className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-dim">{t("ops.budget")}</span>
            <span className="font-semibold tabular-nums text-ink">{t("ops.budgetValue", { spent: budget.spent, budget: budget.budget })}</span>
          </span>
          <div className="mt-2">
            <ProgressBar value={budget.pct} label={t("ops.budget")} tone={budget.pct >= 80 ? "bad" : "brand"} />
          </div>
        </Link>
      )}

      {offline.length > 0 && (
        <p role="status" className="mt-3 rounded-xl border border-warn/40 bg-warn/10 p-3 text-sm leading-relaxed text-ink">
          {t("ops.workersDown", { queues: offline.join(", ") })}
        </p>
      )}

      {live.length > 0 && (
        <>
          <h3 className="mt-4 text-sm font-semibold text-ink">{t("ops.live")}</h3>
          <ul className="mt-2 space-y-2">
            {live.slice(0, 3).map((job) => {
              const shown = job.id === watchingJobId;
              return (
                <li key={job.id} className="rounded-xl border border-brand/40 bg-bg p-3">
                  <div className="flex items-start gap-3">
                    <p className="line-clamp-2 min-w-0 flex-1 text-sm leading-relaxed text-ink" title={job.command}>
                      {job.command}
                    </p>
                    {onWatch &&
                      (shown ? (
                        <span className="shrink-0 rounded-lg px-2 py-1 text-xs font-medium text-cyan">{t("ops.watching")}</span>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onWatch(job.id)}
                          aria-label={`${t("ops.watch")}: ${job.command.slice(0, 80)}`}
                          className="shrink-0 rounded-lg border border-brand/60 px-3 py-1.5 text-sm font-semibold text-brand-soft transition hover:bg-brand/15"
                        >
                          {t("ops.watch")}
                        </button>
                      ))}
                  </div>
                  <div className="mt-2 flex items-center gap-2">
                    <JobStatusBadge status={job.status} />
                    <div className="min-w-0 flex-1">
                      <ProgressBar value={job.progress} label={`${t("ops.live")}: ${job.command.slice(0, 60)}`} />
                    </div>
                    <span className="text-xs tabular-nums text-dim">{Math.round(job.progress)}%</span>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <h3 className="mt-4 text-sm font-semibold text-ink">{t("ops.waitingTitle")}</h3>
      {waiting && waiting.length > 0 ? (
        <ul className="mt-2 space-y-2">
          {waiting.map((job) => (
            <li key={job.id} className="flex items-start gap-3 rounded-xl border border-warn/30 bg-bg p-3">
              <div className="min-w-0 flex-1">
                <p className="line-clamp-2 text-sm leading-relaxed text-ink" title={job.command}>
                  {job.command}
                </p>
                <p className="mt-0.5 text-xs text-dim">
                  {job.finished_at ? t("ops.waitingSince", { ago: timeAgo(job.finished_at, now, locale) }) : dateTime(job.created_at)}
                  {(job.actual_cost_usd ?? job.estimated_cost_usd) != null && ` · ${money(job.actual_cost_usd ?? job.estimated_cost_usd ?? 0)}`}
                </p>
                {(job.qa_verdict || job.demo_mode) && (
                  <p className="mt-1 flex flex-wrap gap-1.5">
                    {job.qa_verdict && (
                      <span
                        className={cx(
                          "rounded-full px-2 py-px text-xs font-medium",
                          job.qa_verdict === "PASS" ? "bg-ok/15 text-ok" : "bg-warn/15 text-warn",
                        )}
                      >
                        {t(job.qa_verdict === "PASS" ? "ops.qaPass" : "ops.qaCheck")}
                      </span>
                    )}
                    {job.demo_mode && <DemoBadge />}
                  </p>
                )}
              </div>
              <Link
                href={`/jobs/${job.id}`}
                aria-label={`${t("ops.review")}: ${job.command.slice(0, 80)}`}
                className="shrink-0 rounded-lg bg-gradient-to-r from-brand to-cyan px-3 py-1.5 text-sm font-semibold text-[#04121f] transition hover:brightness-110"
              >
                {t("ops.review")}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm text-dim">{waiting ? t("ops.noneWaiting") : t("common.loading")}</p>
      )}
      {total > (waiting?.length ?? 0) && (
        <Link href="/jobs?status=awaiting_approval" className="mt-2 inline-block text-sm font-medium text-brand-soft hover:underline">
          {t("ops.viewAll", { n: String(total) })}
        </Link>
      )}
      </>
      )}
    </section>
  );
}

function Stat({ label, value, href, tone }: { label: string; value: number | undefined; href: string; tone: "plain" | "warn" | "bad" }) {
  return (
    <li>
      <Link
        href={href}
        className={cx(
          "flex h-full flex-col rounded-xl border bg-bg p-2.5 transition hover:border-brand-soft/60",
          tone === "warn" ? "border-warn/50" : tone === "bad" ? "border-bad/50" : "border-line",
        )}
      >
        <span className={cx("text-2xl font-bold tabular-nums", tone === "warn" ? "text-warn" : tone === "bad" ? "text-bad" : "text-ink")}>
          {value ?? "–"}
        </span>
        <span className="text-xs leading-snug text-dim">{label}</span>
      </Link>
    </li>
  );
}

/** Mira's next-step ideas: links for things waiting on you, quick actions for useful next jobs. */
export function TaskSuggestions({
  summary,
  onStart,
  disabled = false,
  jobs,
  watchingJobId = null,
  onWatch,
  onRetry,
}: {
  summary: DashboardSummary | null | undefined;
  onStart: (workflow: WorkflowId) => void;
  disabled?: boolean;
  /** Listed jobs from the operations snapshot (live, waiting, failed). */
  jobs?: readonly Job[];
  watchingJobId?: string | null;
  onWatch?: (jobId: string) => void;
  /** Put a failed job's command back in the composer. */
  onRetry?: (command: string) => void;
}) {
  const { t } = useI18n();
  const items = suggestTasks(summary, 4, { jobs, watchingJobId }).filter(
    (s) => (!s.watchJobId || onWatch) && (!s.retryCommand || onRetry),
  );
  const act: Actions = { onStart, onWatch, onRetry };
  if (!summary) return null;
  return (
    <section aria-labelledby="suggest-title" className="rounded-2xl border border-line bg-surface p-4">
      <h2 id="suggest-title" className="text-lg font-semibold text-ink">
        {t("suggest.title")}
      </h2>
      {items.length === 0 ? (
        <p className="mt-1 text-sm text-dim">{t("suggest.none")}</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {items.map((s) => (
            <li key={s.id}>
              <SuggestionItem s={s} act={act} disabled={disabled} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

interface Actions {
  onStart: (w: WorkflowId) => void;
  onWatch?: (jobId: string) => void;
  onRetry?: (command: string) => void;
}

function SuggestionItem({ s, act, disabled }: { s: Suggestion; act: Actions; disabled: boolean }) {
  const { t } = useI18n();
  const text = t(s.label, s.vars);
  const box = cx(
    "flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm leading-relaxed transition",
    s.tone === "warn" ? "border-warn/40 bg-warn/5" : s.tone === "action" ? "border-brand/50 bg-brand/10" : "border-line bg-bg",
  );
  const dot = <span aria-hidden="true" className={cx("size-2 shrink-0 rounded-full", s.tone === "warn" ? "bg-warn" : s.tone === "action" ? "bg-cyan" : "bg-brand-soft")} />;
  const arrow = (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="ml-auto size-4 shrink-0 text-dim" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
  if (s.href)
    return (
      <Link href={s.href} className={cx(box, "text-ink hover:border-brand-soft/60")}>
        {dot}
        <span className="min-w-0">{text}</span>
        {arrow}
      </Link>
    );
  const run = s.workflow
    ? () => act.onStart(s.workflow!)
    : s.watchJobId && act.onWatch
      ? () => act.onWatch!(s.watchJobId!)
      : s.retryCommand && act.onRetry
        ? () => act.onRetry!(s.retryCommand!)
        : null;
  if (run)
    return (
      <button type="button" disabled={disabled} onClick={run} className={cx(box, "text-ink hover:border-brand-soft/60 disabled:opacity-50")}>
        {dot}
        <span className="min-w-0">{text}</span>
        {arrow}
      </button>
    );
  return (
    <p className={cx(box, "text-ink")}>
      {dot}
      <span className="min-w-0">{text}</span>
    </p>
  );
}

/** Shown to an organisation that has never run a job: the three steps from command to approved work. */
export function StartGuide() {
  const { t } = useI18n();
  const steps = [t("start.step1"), t("start.step2"), t("start.step3")];
  return (
    <section aria-labelledby="start-title" className="rounded-2xl border border-cyan/40 bg-cyan/5 p-4">
      <h2 id="start-title" className="text-lg font-semibold text-ink">
        {t("start.title")}
      </h2>
      <ol className="mt-2 space-y-2">
        {steps.map((step, i) => (
          <li key={i} className="flex gap-3 text-sm leading-relaxed text-ink">
            <span aria-hidden="true" className="flex size-6 shrink-0 items-center justify-center rounded-full bg-cyan/20 text-xs font-bold text-cyan">
              {i + 1}
            </span>
            {step}
          </li>
        ))}
      </ol>
    </section>
  );
}
