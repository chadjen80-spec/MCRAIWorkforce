"use client";

// The six Quote-to-Cash figures from GET /v1/qtc/dashboard. Amounts are shown exactly as the server sums them.

import Link from "next/link";
import { EmptyState, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { formatAmount } from "@/lib/qtc";

export interface Figure {
  count: number;
  amount: string;
}

export interface QtcSummary {
  as_of: string;
  month: string;
  pipeline: Figure;
  awaiting_approval: Figure;
  accepted_this_month: Figure;
  outstanding: Figure;
  overdue: Figure;
  cash_received: Figure;
  counts: { open_quotations: number; open_invoices: number };
}

type Key = Exclude<keyof QtcSummary, "as_of" | "month" | "counts">;

const TILES: { key: Key; label: MessageKey; count: MessageKey; href: string; warn?: boolean }[] = [
  { key: "pipeline", label: "qtc.dash.pipeline", count: "qtc.dash.quotes", href: "/qtc/quotations" },
  { key: "awaiting_approval", label: "qtc.dash.awaiting", count: "qtc.dash.quotes", href: "/qtc/quotations?status=pending_approval", warn: true },
  { key: "accepted_this_month", label: "qtc.dash.accepted", count: "qtc.dash.quotes", href: "/qtc/quotations?status=accepted" },
  { key: "outstanding", label: "qtc.dash.outstanding", count: "qtc.dash.invoices", href: "/qtc/invoices?status=open" },
  { key: "overdue", label: "qtc.dash.overdue", count: "qtc.dash.invoices", href: "/qtc/invoices?status=overdue", warn: true },
  { key: "cash_received", label: "qtc.dash.cash", count: "qtc.dash.payments", href: "/qtc/payments" },
];

export function QtcDashboard({ summary }: { summary: QtcSummary }) {
  const { t } = useI18n();
  const empty = TILES.every(({ key }) => summary[key].count === 0);
  return (
    <section aria-labelledby="qtc-dash-title" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="qtc-dash-title" className="text-lg font-semibold text-ink">
          {t("qtc.dash.title")}
        </h2>
        <p className="text-xs text-dim">{t("qtc.dash.asOf", { date: summary.as_of, month: summary.month })}</p>
      </div>
      {empty ? (
        <EmptyState title={t("qtc.dash.emptyTitle")} hint={t("qtc.dash.emptyHint")} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {TILES.map(({ key, label, count, href, warn }) => {
            const fig = summary[key];
            const alert = warn && fig.count > 0;
            return (
              <Link
                key={key}
                href={href}
                className={cx("rounded-2xl border bg-surface p-4 transition hover:border-brand-soft/60", alert ? "border-warn/60" : "border-line")}
              >
                <p className="text-sm text-dim">{t(label)}</p>
                <p className={cx("mt-1 text-2xl font-bold tabular-nums", alert ? "text-warn" : "text-ink")}>{formatAmount(fig.amount)}</p>
                <p className="text-xs text-dim">{t(count, { n: fig.count })}</p>
              </Link>
            );
          })}
        </div>
      )}
    </section>
  );
}
