"use client";

// New invoice: an invoice bills one milestone of an open sales order (deposit, installment or final payment), so
// its amounts come from the order, never typed here. Pick the order, then the milestone, then save it as a draft
// or create and issue it in one step.

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Field, QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Button, Card, EmptyState, LoadingBlock, buttonClass, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { type ApiError, api, toApiError } from "@/lib/api";
import { type Invoice, type Milestone, type Order, type Page, formatAmount } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

/** Milestones that can still be invoiced: billing parts (not the delivery step) without an invoice. */
export function billableMilestones(order: Order | undefined): Milestone[] {
  return (order?.milestones ?? []).filter((m) => m.kind !== "delivery" && m.status === "pending");
}

export default function NewInvoicePage() {
  const { t, date } = useI18n();
  const router = useRouter();
  const params = useSearchParams();
  const orders = useApi<Page<Order>>("qtc:new-invoice:orders", (signal) =>
    api<Page<Order>>("/v1/qtc/orders", { query: { status: "open", limit: 100 }, signal }),
  );
  const [orderId, setOrderId] = useState(params.get("order") ?? "");
  const [milestoneId, setMilestoneId] = useState(params.get("milestone") ?? "");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState<"draft" | "issue" | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  // Set once the draft exists, so a retry after a failed issue does not try to create a second invoice.
  const [createdId, setCreatedId] = useState<string | null>(null);

  const open = (orders.data?.items ?? []).filter((o) => billableMilestones(o).length > 0);
  const order = open.find((o) => o.id === orderId);
  const milestones = billableMilestones(order);
  const milestone = milestones.find((m) => m.id === milestoneId);

  const save = async (issue: boolean) => {
    if (!order || !milestone) return;
    setBusy(issue ? "issue" : "draft");
    setError(null);
    try {
      const id =
        createdId ??
        (await api<Invoice>("/v1/qtc/invoices", { method: "POST", body: { order_id: order.id, milestone_id: milestone.id, notes } })).id;
      setCreatedId(id);
      if (issue) await api<Invoice>(`/v1/qtc/invoices/${id}/issue`, { method: "POST" });
      router.push(`/qtc/invoices/${id}`);
    } catch (e) {
      setError(toApiError(e));
      setBusy(null);
    }
  };

  return (
    <QtcShell>
      {orders.error && !orders.data ? (
        <QtcError error={orders.error} onRetry={orders.reload} />
      ) : !orders.data ? (
        <LoadingBlock />
      ) : open.length === 0 ? (
        <EmptyState
          title={t("qtc.i.newNoOrders")}
          hint={t("qtc.i.newNoOrdersHint")}
          action={
            <Link href="/qtc/orders" className={buttonClass("secondary")}>
              {t("qtc.tab.orders")}
            </Link>
          }
        />
      ) : (
        <Card>
          <h2 className="mb-1 text-lg font-semibold text-ink">{t("qtc.i.new")}</h2>
          <p className="mb-4 text-sm text-dim">{t("qtc.i.newHint")}</p>
          <div className="space-y-4" aria-busy={busy !== null || undefined}>
            {error && <QtcError error={error} />}
            <Field label={t("qtc.i.order")} htmlFor="inv-order">
              <select
                id="inv-order"
                className="input"
                value={orderId}
                disabled={createdId !== null}
                onChange={(e) => {
                  setOrderId(e.target.value);
                  setMilestoneId("");
                }}
              >
                <option value="">{t("qtc.i.chooseOrder")}</option>
                {open.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.number} · {o.customer.name} · {formatAmount(o.grand_total)}
                  </option>
                ))}
              </select>
            </Field>

            {order && (
              <fieldset>
                <legend className="label">{t("qtc.i.milestone")}</legend>
                <ul className="space-y-2">
                  {milestones.map((m) => (
                    <li key={m.id}>
                      <label
                        className={cx(
                          "flex cursor-pointer flex-wrap items-center gap-3 rounded-xl border p-3",
                          milestoneId === m.id ? "border-cyan bg-raised" : "border-line",
                        )}
                      >
                        <input
                          type="radio"
                          name="milestone"
                          value={m.id}
                          checked={milestoneId === m.id}
                          disabled={createdId !== null}
                          onChange={() => setMilestoneId(m.id)}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block font-medium text-ink">
                            {m.position}. {t(`qtc.o.kind.${m.kind}` as MessageKey)}
                            {m.description && m.description !== t(`qtc.o.kind.${m.kind}` as MessageKey) ? ` · ${m.description}` : ""}
                          </span>
                          {m.due_date && <span className="block text-xs text-dim">{t("qtc.i.due", { date: date(m.due_date) })}</span>}
                        </span>
                        <span className="tabular-nums font-semibold text-ink">{formatAmount(m.amount_total)}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </fieldset>
            )}

            {milestone && (
              <dl className="grid grid-cols-3 gap-2 rounded-xl border border-line p-3 text-sm">
                <div>
                  <dt className="text-dim">{t("qtc.i.net")}</dt>
                  <dd className="tabular-nums text-ink">{formatAmount(milestone.amount_net)}</dd>
                </div>
                <div>
                  <dt className="text-dim">{t("qtc.i.vat")}</dt>
                  <dd className="tabular-nums text-ink">{formatAmount(milestone.amount_vat)}</dd>
                </div>
                <div>
                  <dt className="text-dim">{t("qtc.i.total")}</dt>
                  <dd className="tabular-nums font-semibold text-ink">{formatAmount(milestone.amount_total)}</dd>
                </div>
              </dl>
            )}

            <Field label={t("qtc.i.notes")} htmlFor="inv-notes">
              <textarea id="inv-notes" className="input" rows={2} maxLength={4000} value={notes} disabled={createdId !== null} onChange={(e) => setNotes(e.target.value)} />
            </Field>

            <div className="flex flex-wrap gap-2">
              <Button loading={busy === "draft"} disabled={!milestone || busy !== null} onClick={() => void save(false)}>
                {t("qtc.i.createDraft")}
              </Button>
              <Button variant="primary" loading={busy === "issue"} disabled={!milestone || busy !== null} onClick={() => void save(true)}>
                {t("qtc.i.createIssue")}
              </Button>
            </div>
          </div>
        </Card>
      )}
    </QtcShell>
  );
}
