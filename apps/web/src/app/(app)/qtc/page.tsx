"use client";

import Link from "next/link";
import { QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Badge, Card, LoadingBlock } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api } from "@/lib/api";
import { formatRate, type Customer, type Page, type PriceList, type Quotation, type Service } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

interface Overview {
  customers: number;
  quotesWaiting: number;
  services: number;
  priceList: PriceList | null;
}

export default function QtcOverviewPage() {
  const { t, dateTime } = useI18n();
  const data = useApi<Overview>("qtc:overview", async (signal) => {
    const [customers, services, lists, waiting] = await Promise.all([
      api<Page<Customer>>("/v1/qtc/customers", { query: { limit: 1 }, signal }),
      api<Page<Service>>("/v1/qtc/services", { query: { limit: 1 }, signal }),
      api<Page<PriceList>>("/v1/qtc/price-lists", { query: { status: "active", limit: 20 }, signal }),
      api<Page<Quotation>>("/v1/qtc/quotations", { query: { status: "pending_approval", limit: 1 }, signal }),
    ]);
    return { customers: customers.total, quotesWaiting: waiting.total, services: services.total, priceList: lists.items.find((p) => p.is_default) ?? lists.items[0] ?? null };
  });
  const d = data.data;
  const planned = ["qtc.overview.orders", "qtc.overview.invoices", "qtc.overview.dashboard"] as const;

  return (
    <QtcShell>
      {data.error && !d ? (
        <QtcError error={data.error} onRetry={data.reload} />
      ) : !d ? (
        <LoadingBlock />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <Link href="/qtc/quotations" className={`rounded-2xl border bg-surface p-4 transition hover:border-brand-soft/60 ${d.quotesWaiting ? "border-warn/60" : "border-line"}`}>
              <p className={`text-3xl font-bold tabular-nums ${d.quotesWaiting ? "text-warn" : "text-ink"}`}>{d.quotesWaiting}</p>
              <p className="text-sm text-dim">{t("qtc.overview.quotesWaiting")}</p>
            </Link>
            <Link href="/qtc/customers" className="rounded-2xl border border-line bg-surface p-4 transition hover:border-brand-soft/60">
              <p className="text-3xl font-bold tabular-nums text-ink">{d.customers}</p>
              <p className="text-sm text-dim">{t("qtc.overview.customers")}</p>
            </Link>
            <Link href="/qtc/services" className="rounded-2xl border border-line bg-surface p-4 transition hover:border-brand-soft/60">
              <p className="text-3xl font-bold tabular-nums text-ink">{d.services}</p>
              <p className="text-sm text-dim">{t("qtc.overview.services")}</p>
            </Link>
            <Link href="/qtc/services" className="rounded-2xl border border-line bg-surface p-4 transition hover:border-brand-soft/60">
              <p className="text-sm text-dim">{t("qtc.overview.priceList")}</p>
              {d.priceList ? (
                <>
                  <p className="mt-1 font-semibold text-ink">{d.priceList.name}</p>
                  <p className="text-xs text-dim">
                    VAT {formatRate(d.priceList.vat_rate)} · {t(d.priceList.prices_include_vat ? "qtc.priceList.inclusive" : "qtc.priceList.exclusive")}
                    {d.priceList.approved_at && ` · ${dateTime(d.priceList.approved_at)}`}
                  </p>
                </>
              ) : (
                <p className="mt-1 text-sm text-warn">{t("qtc.overview.noPriceList")}</p>
              )}
            </Link>
          </div>
          <Card>
            <h2 className="text-lg font-semibold text-ink">{t("qtc.overview.next")}</h2>
            <p className="text-sm text-dim">{t("qtc.overview.nextHint")}</p>
            <ul className="mt-3 space-y-2">
              {planned.map((key) => (
                <li key={key} className="flex flex-wrap items-center gap-2 text-sm text-dim">
                  <Badge>{t("qtc.planned")}</Badge>
                  {t(key)}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </QtcShell>
  );
}
