"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { OrderStatusBadge } from "@/components/qtc/BillingParts";
import { QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Badge, Button, Card, LoadingBlock, buttonClass } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { type ApiError, api, toApiError } from "@/lib/api";
import { type Invoice, type Order, formatAmount, trimZeros } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

export default function OrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t, date } = useI18n();
  const router = useRouter();
  const order = useApi<Order>(`qtc:order:${id}`, (signal) => api<Order>(`/v1/qtc/orders/${id}`, { signal }));
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const o = order.data;

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  };
  const draftInvoice = (milestoneId: string) =>
    run(milestoneId, async () => {
      const inv = await api<Invoice>("/v1/qtc/invoices", { method: "POST", body: { order_id: id, milestone_id: milestoneId } });
      router.push(`/qtc/invoices/${inv.id}`);
    });

  return (
    <QtcShell>
      {order.error && !o ? (
        <QtcError error={order.error} onRetry={order.reload} />
      ) : !o ? (
        <LoadingBlock />
      ) : (
        <div className="space-y-5">
          <Card>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-ink">{o.number}</h2>
              <OrderStatusBadge status={o.status} />
            </div>
            <p className="text-sm text-dim">
              {o.customer.code} · {o.customer.name} ·{" "}
              <Link className="text-brand-soft hover:underline" href={`/qtc/quotations/${o.quotation_id}`}>
                {t("qtc.o.fromQuote", { number: o.quotation_number, rev: String(o.quotation_revision) })}
              </Link>
            </p>
            <ul className="mt-3 divide-y divide-line/60 text-sm">
              {o.lines.map((l) => (
                <li key={l.position} className="flex justify-between gap-4 py-1.5">
                  <span className="text-ink">
                    {l.description} · {trimZeros(l.quantity)} {l.unit}
                  </span>
                  <span className="tabular-nums text-ink">{formatAmount(l.line_total)}</span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-right font-semibold tabular-nums text-ink">฿{formatAmount(o.grand_total)}</p>
            <p className="text-right text-xs text-dim">
              {t("qtc.o.invoiced", { amount: `฿${formatAmount(o.invoiced_total)}` })} · {t("qtc.o.paid", { amount: `฿${formatAmount(o.paid_total)}` })}
            </p>
          </Card>
          {error && <QtcError error={error} />}
          <Card>
            <h3 className="mb-2 font-semibold text-ink">{t("qtc.o.billing")}</h3>
            <ul className="space-y-2">
              {o.milestones
                .filter((m) => m.kind !== "delivery")
                .map((m) => (
                  <li key={m.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-bg p-3 text-sm">
                    <Badge>{t(`qtc.o.kind.${m.kind}` as MessageKey)}</Badge>
                    <span className="min-w-0 flex-1 text-ink">
                      {m.description}
                      {m.due_date && <span className="block text-xs text-dim">{t("qtc.i.due", { date: date(m.due_date) })}</span>}
                    </span>
                    <span className="tabular-nums text-ink">฿{formatAmount(m.amount_total)}</span>
                    {m.invoice_id ? (
                      <Link className={buttonClass("secondary", "sm")} href={`/qtc/invoices/${m.invoice_id}`}>
                        {t("qtc.o.viewInvoice")}
                      </Link>
                    ) : (
                      o.status === "open" &&
                      m.status === "pending" && (
                        <Button size="sm" variant="primary" loading={busy === m.id} onClick={() => void draftInvoice(m.id)}>
                          {t("qtc.o.makeInvoice")}
                        </Button>
                      )
                    )}
                  </li>
                ))}
            </ul>
          </Card>
          {o.milestones.some((m) => m.kind === "delivery") && (
            <Card>
              <h3 className="mb-2 font-semibold text-ink">{t("qtc.o.delivery")}</h3>
              <ul className="space-y-2">
                {o.milestones
                  .filter((m) => m.kind === "delivery")
                  .map((m) => (
                    <li key={m.id} className="flex flex-wrap items-center gap-3 text-sm">
                      <span className="min-w-0 flex-1 text-ink">{m.description}</span>
                      {m.status === "done" ? (
                        <Badge tone="success">{t("qtc.o.done")}</Badge>
                      ) : (
                        o.status !== "cancelled" && (
                          <Button size="sm" loading={busy === m.id} onClick={() => void run(m.id, async () => {
                            await api(`/v1/qtc/orders/${id}/milestones/${m.id}/done`, { method: "POST" });
                            order.reload();
                          })}>
                            {t("qtc.o.markDone")}
                          </Button>
                        )
                      )}
                    </li>
                  ))}
              </ul>
            </Card>
          )}
          {o.status === "open" && (
            <Button variant="ghost" loading={busy === "cancel"} onClick={() => void run("cancel", async () => {
              await api(`/v1/qtc/orders/${id}/cancel`, { method: "POST", body: { reason: "" } });
              order.reload();
            })}>
              {t("qtc.o.cancel")}
            </Button>
          )}
        </div>
      )}
    </QtcShell>
  );
}
