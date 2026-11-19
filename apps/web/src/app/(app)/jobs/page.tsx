"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { JobRow } from "@/components/status";
import { Button, EmptyState, ErrorState, LoadingBlock, PageHeader, Pagination, RefreshError, buttonClass } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, toApiError } from "@/lib/api";
import { JOB_STATUSES, type Job, type JobStatus, type Paginated } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const LIMIT = 20;

/** A ?status= value from the URL, or "" (all) when it is missing or not a real job status. */
function statusFromParam(value: string | null): JobStatus | "" {
  return value && (JOB_STATUSES as readonly string[]).includes(value) ? (value as JobStatus) : "";
}

export default function JobsPage() {
  const { t } = useI18n();
  // Deep links such as /jobs?status=awaiting_approval (from the Assistant's operations panel) open filtered.
  const params = useSearchParams();
  const [status, setStatus] = useState<JobStatus | "">(() => statusFromParam(params.get("status")));
  const [offset, setOffset] = useState(0);

  const jobs = useApi<Paginated<Job>>(
    `jobs:${status}:${offset}`,
    (signal) => api<Paginated<Job>>("/v1/jobs", { query: { status, limit: LIMIT, offset }, signal }),
    { interval: 10000 },
  );

  // Delete every finished job in the current list (respects the status filter). Jobs that are
  // still in progress are skipped by the server.
  const [clearing, setClearing] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const total = jobs.data?.total ?? 0;
  const deleteAll = async () => {
    if (!window.confirm(t("jobs.deleteAllConfirm", { n: total }))) return;
    setClearing(true);
    setNotice(null);
    try {
      const result = await api<{ deleted: number; skipped_active: number }>("/v1/jobs", { method: "DELETE", query: { status } });
      setNotice({
        tone: "ok",
        text: t("jobs.deleteAllDone", { n: result.deleted }) + (result.skipped_active ? t("jobs.deleteAllSkipped", { n: result.skipped_active }) : ""),
      });
      setOffset(0);
      jobs.reload();
    } catch (e) {
      setNotice({ tone: "bad", text: t("jobs.deleteFailed", { message: toApiError(e).message }) });
    }
    setClearing(false);
  };

  return (
    <>
      <PageHeader
        title={t("jobs.title")}
        subtitle={t("jobs.subtitle")}
        actions={
          <Link href="/workforce" className={buttonClass("primary")}>
            {t("dashboard.newCommand")}
          </Link>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <label htmlFor="status-filter" className="text-sm text-dim">
          {t("jobs.filter")}
        </label>
        <select
          id="status-filter"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value as JobStatus | "");
            setOffset(0);
          }}
          className="input w-auto min-w-44"
        >
          <option value="">{t("common.all")}</option>
          {JOB_STATUSES.map((s) => (
            <option key={s} value={s}>
              {t(`status.${s}`)}
            </option>
          ))}
        </select>
        {total > 0 && (
          <Button variant="danger" loading={clearing} onClick={deleteAll} className="ml-auto">
            {status ? t("jobs.deleteAllFiltered", { status: t(`status.${status}`) }) : t("jobs.deleteAll")}
          </Button>
        )}
      </div>
      {notice && (
        <p role="status" className={notice.tone === "ok" ? "mb-4 text-sm text-ok" : "mb-4 text-sm text-bad"}>
          {notice.text}
        </p>
      )}

      {jobs.loading && <LoadingBlock />}
      {jobs.error && !jobs.data && <ErrorState error={jobs.error} onRetry={jobs.reload} />}
      {jobs.error && jobs.data && <RefreshError error={jobs.error} onRetry={jobs.reload} />}

      {jobs.data &&
        (jobs.data.items.length === 0 ? (
          <EmptyState
            title={status ? t("jobs.emptyFiltered") : t("jobs.empty")}
            hint={status ? undefined : t("jobs.emptyHint")}
          />
        ) : (
          <>
            <ul className="space-y-3">
              {jobs.data.items.map((job) => (
                <JobRow key={job.id} job={job} onDeleted={jobs.reload} />
              ))}
            </ul>
            <Pagination total={jobs.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        ))}
    </>
  );
}
