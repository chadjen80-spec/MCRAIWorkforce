"use client";

import Link from "next/link";
import { useState } from "react";
import { OrderStatusBadge } from "@/components/qtc/BillingParts";
import { QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { EmptyState, LoadingBlock, Pagination } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api } from "@/lib/api";
import { type Order, type Page, formatAmount } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

const LIMIT = 20;

export default function OrdersPage() {
  const { t, date } = useI18n();
  const [offset, setOffset] = useState(0);
  const list = useApi<Page<Order>>(`qtc:orders:${offset}`, (signal) => api<Page<Order>>("/v1/qtc/orders", { query: { limit: LIMIT, offset }, signal }));
  return (
    <QtcShell>
      {list.error && !list.data ? (
        <QtcError error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingBlock />
      ) : list.data.items.length === 0 ? (
        <EmptyState title={t("qtc.o.empty")} hint={t("qtc.o.emptyHint")} />
      ) : (
        <>
          <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
            {list.data.items.map((o) => (
              <li key={o.id}>
                <Link href={`/qtc/orders/${o.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 transition hover:bg-raised">
                  <span className="w-32 shrink-0 font-mono text-xs text-dim">{o.number}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium text-ink">{o.customer.name}</span>
                    <span className="block text-xs text-dim">
                      {o.quotation_number} · {date(o.created_at.slice(0, 10))}
                    </span>
                  </span>
                  <span className="text-xs text-dim">{t("qtc.o.paid", { amount: `฿${formatAmount(o.paid_total)}` })}</span>
                  <span className="text-sm tabular-nums text-ink">฿{formatAmount(o.grand_total)}</span>
                  <OrderStatusBadge status={o.status} />
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
