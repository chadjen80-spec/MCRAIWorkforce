"use client";

import Link from "next/link";
import { ArtifactGrid } from "@/components/artifacts";
import { JobRow } from "@/components/status";
import {
  Card,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  ProgressBar,
  RefreshError,
  SectionTitle,
  buttonClass,
  cx,
} from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, stabilizeArtifacts } from "@/lib/api";
import type { DashboardSummary, Job, Paginated } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const REFRESH_MS = 15000;

function Stat({ label, value, accent }: { label: string; value: string; accent: string }) {
  return (
    <div className="rounded-2xl border border-line bg-surface p-4">
      <p className="flex items-center gap-2 text-sm text-dim">
        <span aria-hidden="true" className={cx("size-2 rounded-full", accent)} />
        {label}
      </p>
      <p className="mt-2 text-3xl font-semibold tabular-nums text-ink">{value}</p>
    </div>
  );
}

export default function DashboardPage() {
  const { t, money, number } = useI18n();

  const summary = useApi<DashboardSummary>(
    "dashboard-summary",
    async (signal) => {
      const s = await api<DashboardSummary>("/v1/dashboard/summary", { signal });
      return { ...s, recent_artifacts: stabilizeArtifacts(s.recent_artifacts) };
    },
    { interval: REFRESH_MS },
  );
  const jobs = useApi<Paginated<Job>>(
    "dashboard-jobs",
    (signal) => api<Paginated<Job>>("/v1/jobs", { query: { limit: 5 }, signal }),
    { interval: REFRESH_MS },
  );

  const s = summary.data;
  const hasBudget = !!s && s.monthly_budget_usd > 0;
  const pct = s && hasBudget ? (s.month_cost_usd / s.monthly_budget_usd) * 100 : 0;

  return (
    <>
      <PageHeader
        title={t("dashboard.title")}
        subtitle={t("dashboard.subtitle")}
        actions={
          <Link href="/workforce" className={buttonClass("primary")}>
            {t("dashboard.newCommand")}
          </Link>
        }
      />

      {summary.loading && <LoadingBlock />}
      {summary.error && !s && <ErrorState error={summary.error} onRetry={summary.reload} />}
      {summary.error && s && <RefreshError error={summary.error} onRetry={summary.reload} />}
      {s && (s.offline_queues?.length ?? 0) > 0 && (
        <p role="alert" className="rounded-xl border border-warn/50 bg-warn/10 p-4 text-sm text-ink">
          <strong className="font-semibold">{t("dashboard.workersDown")}</strong>{" "}
          {t("dashboard.workersDownHint", { queues: (s.offline_queues ?? []).join(", ") })}
        </p>
      )}

      {s && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
            <Stat label={t("dashboard.active")} value={number(s.active_jobs)} accent="bg-brand" />
            <Stat label={t("dashboard.completed")} value={number(s.completed_jobs)} accent="bg-ok" />
            <Stat label={t("dashboard.failed")} value={number(s.failed_jobs)} accent="bg-bad" />
            <Stat label={t("dashboard.awaiting")} value={number(s.awaiting_approval_jobs)} accent="bg-warn" />
          </div>

          <Card className="mt-4">
            <SectionTitle>{t("dashboard.costTitle")}</SectionTitle>
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-3xl font-semibold tabular-nums text-ink">{money(s.month_cost_usd)}</span>
              <span className="text-sm text-dim">
                {hasBudget ? t("dashboard.costOf", { budget: money(s.monthly_budget_usd) }) : t("dashboard.noBudget")}
              </span>
            </div>
            {hasBudget && (
              <div className="mt-3">
                <ProgressBar value={pct} label={t("dashboard.costTitle")} tone={pct > 100 ? "bad" : "brand"} />
                {pct > 100 && <p className="mt-2 text-sm text-bad">{t("dashboard.overBudget")}</p>}
              </div>
            )}
            <p className="mt-3 text-sm text-dim">
              {t("dashboard.activeEstimate")}: <span className="tabular-nums text-ink">{money(s.active_estimated_cost_usd)}</span>
            </p>
          </Card>
        </>
      )}

      <div className="mt-8 grid gap-8 xl:grid-cols-5">
        <div className="xl:col-span-3">
          <SectionTitle
            aside={
              <Link href="/deliverables" className="text-sm text-brand-soft hover:underline">
                {t("common.viewAll")}
              </Link>
            }
          >
            {t("dashboard.recentDeliverables")}
          </SectionTitle>
          {s &&
            (s.recent_artifacts.length === 0 ? (
              <EmptyState title={t("dashboard.noDeliverables")} hint={t("dashboard.noDeliverablesHint")} />
            ) : (
              <ArtifactGrid artifacts={s.recent_artifacts} showJobLink />
            ))}
          {!s && !summary.loading && summary.error && (
            <p className="text-sm text-dim">
              {t("error.title")} · <code className="font-mono">{summary.error.code}</code>
            </p>
          )}
        </div>

        <div className="xl:col-span-2">
          <SectionTitle
            aside={
              <Link href="/jobs" className="text-sm text-brand-soft hover:underline">
                {t("common.viewAll")}
              </Link>
            }
          >
            {t("dashboard.latestJobs")}
          </SectionTitle>
          {jobs.loading && <LoadingBlock />}
          {jobs.error && !jobs.data && <ErrorState error={jobs.error} onRetry={jobs.reload} />}
          {jobs.error && jobs.data && <RefreshError error={jobs.error} onRetry={jobs.reload} />}
          {jobs.data &&
            (jobs.data.items.length === 0 ? (
              <EmptyState
                title={t("dashboard.noJobs")}
                hint={t("dashboard.noJobsHint")}
                action={
                  <Link href="/workforce" className={buttonClass("secondary")}>
                    {t("dashboard.newCommand")}
                  </Link>
                }
              />
            ) : (
              <ul className="space-y-3">
                {jobs.data.items.map((job) => (
                  <JobRow key={job.id} job={job} />
                ))}
              </ul>
            ))}
        </div>
      </div>
    </>
  );
}
