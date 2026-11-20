"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { InvoiceStatusBadge, PaymentStatusBadge } from "@/components/qtc/BillingParts";
import { QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Button, Card, LoadingBlock, buttonClass } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { type ApiError, api, toApiError } from "@/lib/api";
import { type Invoice, formatAmount, formatRate, trimZeros } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t, date } = useI18n();
  const invoice = useApi<Invoice>(`qtc:invoice:${id}`, (signal) => api<Invoice>(`/v1/qtc/invoices/${id}`, { signal }));
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const i = invoice.data;
  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    setError(null);
    try {
      await api(`/v1/qtc/invoices/${id}${path}`, { method: "POST", body });
      invoice.reload();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <QtcShell>
      {invoice.error && !i ? (
        <QtcError error={invoice.error} onRetry={invoice.reload} />
      ) : !i ? (
        <LoadingBlock />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <Card>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-ink">{i.number ?? t("qtc.i.status.draft")}</h2>
              <InvoiceStatusBadge status={i.status} />
            </div>
            <p className="text-sm text-dim">
              {i.customer.code} · {i.customer.name} ·{" "}
              <Link className="text-brand-soft hover:underline" href={`/qtc/orders/${i.order_id}`}>
                {i.order_number}
              </Link>
              {i.issue_date && ` · ${date(i.issue_date)}`}
              {i.due_date && ` · ${t("qtc.i.due", { date: date(i.due_date) })}`}
            </p>
            <p className="mt-3 rounded-xl border border-line bg-bg p-3 text-xs leading-relaxed text-dim">{t("qtc.i.notTax")}</p>
            <ul className="mt-3 divide-y divide-line/60 text-sm">
              {i.lines.map((l) => (
                <li key={l.position} className="flex justify-between gap-4 py-1.5">
                  <span className="text-ink">
                    {l.description} · {trimZeros(l.quantity)} {l.unit}
                  </span>
                  <span className="tabular-nums text-ink">{formatAmount(l.line_total)}</span>
                </li>
              ))}
            </ul>
            <dl className="ml-auto mt-3 max-w-sm space-y-1 text-sm">
              <div className="flex justify-between">
                <dt className="text-dim">{t("qtc.q.subtotal")}</dt>
                <dd className="tabular-nums">{formatAmount(i.subtotal)}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-dim">{t("qtc.q.vat", { rate: formatRate(i.vat_rate) })}</dt>
                <dd className="tabular-nums">{formatAmount(i.vat_total)}</dd>
              </div>
              <div className="flex justify-between border-t border-line pt-1 font-semibold">
                <dt>{t("qtc.q.grandTotal")}</dt>
                <dd className="tabular-nums">฿{formatAmount(i.total)}</dd>
              </div>
              <p className="text-right text-xs text-dim">({i.amount_in_words})</p>
              <div className="flex justify-between">
                <dt className="text-dim">{t("qtc.i.paid")}</dt>
                <dd className="tabular-nums text-ok">{formatAmount(i.paid_amount)}</dd>
              </div>
              <div className="flex justify-between font-semibold">
                <dt>{t("qtc.i.balance")}</dt>
                <dd className="tabular-nums text-warn">฿{formatAmount(i.balance)}</dd>
              </div>
              {i.pending_payments !== "0.00" && <p className="text-right text-xs text-warn">{t("qtc.i.pending", { amount: `฿${formatAmount(i.pending_payments)}` })}</p>}
            </dl>
          </Card>
          <div className="space-y-4">
            {error && <QtcError error={error} />}
            <Card>
              <h3 className="mb-2 font-semibold text-ink">{t("qtc.q.download")}</h3>
              <div className="flex gap-2">
                <a className={buttonClass("secondary", "sm")} href={`/api/proxy/v1/qtc/invoices/${i.id}/document?format=pdf`}>
                  PDF
                </a>
                <a className={buttonClass("secondary", "sm")} href={`/api/proxy/v1/qtc/invoices/${i.id}/document?format=docx`}>
                  DOCX
                </a>
              </div>
              {i.status === "draft" && <p className="mt-2 text-xs text-dim">{t("qtc.q.draftDoc")}</p>}
            </Card>
            <Card>
              <div className="flex flex-col gap-2">
                {i.status === "draft" && (
                  <>
                    <p className="text-xs text-dim">{t("qtc.i.issueHint")}</p>
                    <Button variant="primary" loading={busy} onClick={() => void act("/issue")}>
                      {t("qtc.i.issue")}
                    </Button>
                  </>
                )}
                {["issued", "partially_paid", "overdue"].includes(i.status) && (
                  <Link className={buttonClass("primary")} href={`/qtc/payments?customer=${i.customer.id}&invoice=${i.id}`}>
                    {t("qtc.i.recordPayment")}
                  </Link>
                )}
                {["draft", "issued", "overdue"].includes(i.status) && i.paid_amount === "0.00" && (
                  <>
                    <p className="text-xs text-dim">{t("qtc.i.cancelHint")}</p>
                    <Button variant="ghost" loading={busy} onClick={() => void act("/cancel", { reason: "" })}>
                      {t("qtc.i.cancel")}
                    </Button>
                  </>
                )}
              </div>
            </Card>
            {i.allocations.length > 0 && (
              <Card>
                <h3 className="mb-2 font-semibold text-ink">{t("qtc.i.payments")}</h3>
                <ul className="space-y-2 text-sm">
                  {i.allocations.map((a) => (
                    <li key={a.payment_id} className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-dim">{a.payment_number}</span>
                      <span className="tabular-nums text-ink">฿{formatAmount(a.amount)}</span>
                      <PaymentStatusBadge status={a.status as "confirmed"} />
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        </div>
      )}
    </QtcShell>
  );
}
