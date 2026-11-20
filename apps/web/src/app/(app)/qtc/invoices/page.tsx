"use client";

import Link from "next/link";
import { useState } from "react";
import { InvoiceStatusBadge } from "@/components/qtc/BillingParts";
import { QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { EmptyState, LoadingBlock, Pagination, buttonClass } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { api } from "@/lib/api";
import { type Invoice, type Page, formatAmount } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

const LIMIT = 20;
const FILTERS = ["all", "open", "overdue", "draft", "partially_paid", "paid", "cancelled"] as const;

export default function InvoicesPage() {
  const { t, date } = useI18n();
  const [status, setStatus] = useState<(typeof FILTERS)[number]>("all");
  const [offset, setOffset] = useState(0);
  const list = useApi<Page<Invoice>>(`qtc:invoices:${status}:${offset}`, (signal) =>
    api<Page<Invoice>>("/v1/qtc/invoices", { query: { status: status === "all" ? undefined : status, limit: LIMIT, offset }, signal }),
  );
  return (
    <QtcShell
      actions={
        <Link href="/qtc/invoices/new" className={buttonClass("primary")}>
          {t("qtc.i.new")}
        </Link>
      }
    >
      <p className="mb-4 rounded-xl border border-line bg-surface p-3 text-xs leading-relaxed text-dim">{t("qtc.i.notTax")}</p>
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
            {t(`qtc.i.status.${f}` as MessageKey)}
          </button>
        ))}
      </div>
      {list.error && !list.data ? (
        <QtcError error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingBlock />
      ) : list.data.items.length === 0 ? (
        <EmptyState title={t("qtc.i.empty")} hint={t("qtc.i.emptyHint")} />
      ) : (
        <>
          <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
            {list.data.items.map((i) => (
              <li key={i.id}>
                <Link href={`/qtc/invoices/${i.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 transition hover:bg-raised">
                  <span className="w-32 shrink-0 font-mono text-xs text-dim">{i.number ?? t("qtc.i.status.draft")}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium text-ink">{i.customer.name}</span>
                    <span className="block text-xs text-dim">
                      {i.order_number}
                      {i.due_date && ` · ${t("qtc.i.due", { date: date(i.due_date) })}`}
                    </span>
                  </span>
                  <span className="text-right text-sm tabular-nums">
                    <span className="block text-ink">฿{formatAmount(i.total)}</span>
                    {i.balance !== "0.00" && i.status !== "draft" && i.status !== "cancelled" && (
                      <span className="block text-xs text-warn">
                        {t("qtc.i.balance")} ฿{formatAmount(i.balance)}
                      </span>
                    )}
                  </span>
                  <InvoiceStatusBadge status={i.status} />
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
