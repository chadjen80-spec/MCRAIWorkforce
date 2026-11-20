"use client";

import Link from "next/link";
import { useState } from "react";
import { QuotationStatusBadge } from "@/components/qtc/QuotationParts";
import { QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { EmptyState, LoadingBlock, Pagination, RefreshError, buttonClass } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { api } from "@/lib/api";
import { type Page, type Quotation, formatAmount } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

const LIMIT = 20;
const FILTERS = ["all", "draft", "pending_approval", "approved", "sent", "accepted", "expired", "declined", "cancelled"] as const;

export default function QuotationsPage() {
  const { t, date } = useI18n();
  const [status, setStatus] = useState<(typeof FILTERS)[number]>("all");
  const [offset, setOffset] = useState(0);
  const list = useApi<Page<Quotation>>(`qtc:quotations:${status}:${offset}`, (signal) =>
    api<Page<Quotation>>("/v1/qtc/quotations", { query: { status: status === "all" ? undefined : status, limit: LIMIT, offset }, signal }),
  );

  return (
    <QtcShell
      actions={
        <Link href="/qtc/quotations/new" className={buttonClass("primary")}>
          {t("qtc.q.new")}
        </Link>
      }
    >
      <div role="group" aria-label={t("jobs.filter")} className="mb-4 flex flex-wrap gap-1.5">
        {FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={status === f}
            onClick={() => {
              setStatus(f);
              setOffset(0);
            }}
            className={`rounded-full border px-3 py-1 text-sm transition ${status === f ? "border-cyan bg-raised text-ink" : "border-line text-dim hover:text-ink"}`}
          >
            {t(`qtc.q.status.${f}` as MessageKey)}
          </button>
        ))}
      </div>
      {list.error && !list.data ? (
        <QtcError error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingBlock />
      ) : list.data.items.length === 0 ? (
        <EmptyState title={t("qtc.q.empty")} hint={t("qtc.q.emptyHint")} />
      ) : (
        <>
          {list.error && <RefreshError error={list.error} onRetry={list.reload} />}
          <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
            {list.data.items.map((q) => (
              <li key={q.id}>
                <Link href={`/qtc/quotations/${q.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 transition hover:bg-raised">
                  <span className="w-32 shrink-0 font-mono text-xs text-dim">
                    {q.number}
                    {q.revision > 1 && ` r${q.revision}`}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium text-ink">{q.title || q.customer.name}</span>
                    <span className="block text-xs text-dim">
                      {q.customer.name} · {t("qtc.q.validUntil")} {date(q.valid_until)}
                    </span>
                  </span>
                  {q.missing_prices > 0 && <span className="text-xs text-warn">{t("qtc.q.needsPrice")}</span>}
                  <span className="text-sm tabular-nums text-ink">฿{formatAmount(q.grand_total)}</span>
                  <QuotationStatusBadge status={q.status} />
                </Link>
              </li>
            ))}
          </ul>
          <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </QtcShell>
  );
}
