"use client";

import Link from "next/link";
import { ProgressBar, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { DashboardSummary, Job } from "@/lib/types";
import { suggestTasks, type Suggestion, type WorkflowId } from "@/lib/workflows";

/**
 * What needs the manager's attention right now: jobs waiting for approval (each with a direct Review link),
 * work in progress, failures, budget and offline workers. Every number links to the list behind it.
 */
export function OpsSummary({
  summary,
  waiting,
  waitingTotal,
  failed = false,
}: {
  summary: DashboardSummary | null | undefined;
  /** The newest jobs awaiting approval (a few). */
  waiting: Job[] | undefined;
  waitingTotal?: number;
  /** The status could not be loaded. */
  failed?: boolean;
}) {
  const { t, money, dateTime } = useI18n();
  const s = summary;
  const budgetPct = s && s.monthly_budget_usd > 0 ? Math.min(100, (s.month_cost_usd / s.monthly_budget_usd) * 100) : 0;
  const offline = s?.offline_queues ?? [];
  const total = waitingTotal ?? s?.awaiting_approval_jobs ?? 0;

  return (
    <section aria-labelledby="ops-title" className="rounded-2xl border border-line bg-surface p-4">
      <h2 id="ops-title" className="text-lg font-semibold text-ink">
        {t("ops.title")}
      </h2>
      {failed && !s && (
        <p role="status" className="mt-2 text-sm text-bad">
          {t("ops.loadFailed")}
        </p>
      )}

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

      {s && (
        <Link href="/dashboard" className="mt-3 block rounded-xl border border-line bg-bg p-3 transition hover:border-brand-soft/60">
          <span className="flex items-baseline justify-between gap-2 text-sm">
            <span className="text-dim">{t("ops.budget")}</span>
            <span className="font-semibold tabular-nums text-ink">
              {t("ops.budgetValue", { spent: s.month_cost_usd.toFixed(2), budget: s.monthly_budget_usd.toFixed(0) })}
            </span>
          </span>
          <div className="mt-2">
            <ProgressBar value={budgetPct} label={t("ops.budget")} tone={budgetPct >= 80 ? "bad" : "brand"} />
          </div>
        </Link>
      )}

      {offline.length > 0 && (
        <p role="status" className="mt-3 rounded-xl border border-warn/40 bg-warn/10 p-3 text-sm leading-relaxed text-ink">
          {t("ops.workersDown", { queues: offline.join(", ") })}
        </p>
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
                  {dateTime(job.created_at)}
                  {(job.actual_cost_usd ?? job.estimated_cost_usd) != null && ` · ${money(job.actual_cost_usd ?? job.estimated_cost_usd ?? 0)}`}
                </p>
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
}: {
  summary: DashboardSummary | null | undefined;
  onStart: (workflow: WorkflowId) => void;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const items = suggestTasks(summary);
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
              <SuggestionItem s={s} onStart={onStart} disabled={disabled} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SuggestionItem({ s, onStart, disabled }: { s: Suggestion; onStart: (w: WorkflowId) => void; disabled: boolean }) {
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
  if (s.workflow)
    return (
      <button type="button" disabled={disabled} onClick={() => onStart(s.workflow!)} className={cx(box, "text-ink hover:border-brand-soft/60 disabled:opacity-50")}>
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
