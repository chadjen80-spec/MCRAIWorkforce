"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { PaymentStatusBadge } from "@/components/qtc/BillingParts";
import { Field, QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Button, Card, EmptyState, LoadingBlock, Pagination, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { ApiError, api, newIdempotencyKey, toApiError } from "@/lib/api";
import { type Customer, type Invoice, type Page, type Payment, formatAmount, parseAmountInput } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

const LIMIT = 20;
const METHODS = ["bank_transfer", "promptpay", "cash", "cheque", "card", "other"] as const;

/** Exact subtraction on 2-decimal strings (no floats): received - allocations, in satang. */
function satang(v: string | null): bigint {
  if (!v) return BigInt(0);
  const [i, f = ""] = v.split(".");
  return BigInt(i) * BigInt(100) + BigInt(f.padEnd(2, "0").slice(0, 2));
}
const toBaht = (s: bigint) => `${s < 0 ? "-" : ""}${(s < 0 ? -s : s) / BigInt(100)}.${String((s < 0 ? -s : s) % BigInt(100)).padStart(2, "0")}`;

export default function PaymentsPage() {
  const { t } = useI18n();
  const params = useSearchParams();
  const [status, setStatus] = useState<"all" | Payment["status"]>("all");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(Boolean(params.get("customer")));
  const [error, setError] = useState<ApiError | null>(null);
  const list = useApi<Page<Payment>>(`qtc:payments:${status}:${offset}`, (signal) =>
    api<Page<Payment>>("/v1/qtc/payments", { query: { status: status === "all" ? undefined : status, limit: LIMIT, offset }, signal }),
  );
  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      list.reload();
    } catch (e) {
      setError(toApiError(e));
    }
  };
  const upload = (p: Payment, file: File) =>
    run(async () => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`/api/proxy/v1/qtc/payments/${p.id}/evidence`, { method: "POST", body: form, credentials: "same-origin" });
      if (!res.ok) {
        const body = await res.json().catch(() => undefined);
        throw new ApiError(res.status, body?.error?.code ?? "UPLOAD_FAILED", body?.error?.message ?? res.statusText);
      }
    });

  return (
    <QtcShell actions={!creating && <Button variant="primary" onClick={() => setCreating(true)}>{t("qtc.p.new")}</Button>}>
      <p className="mb-4 rounded-xl border border-warn/40 bg-warn/5 p-3 text-xs leading-relaxed text-ink">{t("qtc.p.hint")}</p>
      {creating && (
        <RecordPayment
          initialCustomer={params.get("customer") ?? ""}
          initialInvoice={params.get("invoice") ?? ""}
          onDone={() => {
            setCreating(false);
            list.reload();
          }}
          onCancel={() => setCreating(false)}
        />
      )}
      {error && <QtcError error={error} />}
      <div role="group" aria-label={t("jobs.filter")} className="mb-4 flex flex-wrap gap-1.5">
        {(["all", "pending_confirmation", "confirmed", "rejected"] as const).map((f) => (
          <button
            key={f}
            type="button"
            aria-pressed={status === f}
            onClick={() => {
              setStatus(f);
              setOffset(0);
            }}
            className={cx("rounded-full border px-3 py-1 text-sm transition", status === f ? "border-cyan bg-raised text-ink" : "border-line text-dim hover:text-ink")}
          >
            {t(`qtc.p.status.${f}` as MessageKey)}
          </button>
        ))}
      </div>
      {list.error && !list.data ? (
        <QtcError error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingBlock />
      ) : list.data.items.length === 0 ? (
        <EmptyState title={t("qtc.p.empty")} />
      ) : (
        <>
          <ul className="space-y-3">
            {list.data.items.map((p) => (
              <li key={p.id} className="rounded-2xl border border-line bg-surface p-4">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span className="font-mono text-xs text-dim">{p.number}</span>
                  <span className="min-w-0 flex-1 font-medium text-ink">{p.customer.name}</span>
                  <span className="tabular-nums text-ink">฿{formatAmount(p.amount)}</span>
                  <PaymentStatusBadge status={p.status} />
                </div>
                <p className="mt-1 text-xs text-dim">
                  {p.received_on} · {t(`qtc.p.method.${p.method}` as MessageKey)}
                  {p.reference && ` · ${p.reference}`} · {p.allocations.map((a) => `${a.invoice_number ?? "?"} ฿${formatAmount(a.amount)}`).join(", ")}
                </p>
                {p.unallocated !== "0.00" && <p className="text-xs text-warn">{t("qtc.p.unallocated", { amount: formatAmount(p.unallocated) })}</p>}
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {p.has_evidence ? (
                    <a className="text-xs text-brand-soft hover:underline" href={`/api/proxy/v1/qtc/payments/${p.id}/evidence`} target="_blank" rel="noreferrer">
                      {t("qtc.p.viewEvidence")} ({p.evidence_name})
                    </a>
                  ) : (
                    <label className="cursor-pointer text-xs text-brand-soft hover:underline">
                      {t("qtc.p.evidence")}
                      <input type="file" accept="application/pdf,image/jpeg,image/png,image/webp" className="sr-only" onChange={(e) => e.target.files?.[0] && void upload(p, e.target.files[0])} />
                    </label>
                  )}
                  {p.status === "pending_confirmation" && (
                    <>
                      <Button size="sm" variant="primary" onClick={() => void run(() => api(`/v1/qtc/payments/${p.id}/confirm`, { method: "POST" }))}>
                        {t("qtc.p.confirm")}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void run(() => api(`/v1/qtc/payments/${p.id}/reject`, { method: "POST", body: { reason: "" } }))}>
                        {t("qtc.p.reject")}
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
          <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </QtcShell>
  );
}

function RecordPayment({ initialCustomer, initialInvoice, onDone, onCancel }: { initialCustomer: string; initialInvoice: string; onDone: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const [customerId, setCustomerId] = useState(initialCustomer);
  const [amount, setAmount] = useState("");
  const [receivedOn, setReceivedOn] = useState(() => new Date().toISOString().slice(0, 10));
  const [method, setMethod] = useState<(typeof METHODS)[number]>("bank_transfer");
  const [reference, setReference] = useState("");
  const [alloc, setAlloc] = useState<Record<string, string>>({});
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const [idemKey] = useState(newIdempotencyKey);
  const customers = useApi<Page<Customer>>("qtc:pay-customers", (signal) => api<Page<Customer>>("/v1/qtc/customers", { query: { limit: 100 }, signal }));
  const open = useApi<Page<Invoice>>(customerId ? `qtc:open-invoices:${customerId}` : null, (signal) =>
    api<Page<Invoice>>("/v1/qtc/invoices", { query: { status: "open", customer_id: customerId, limit: 50 }, signal }),
  );
  const received = parseAmountInput(amount);
  const parts = Object.values(alloc).filter((v) => v.trim());
  const partsValid = parts.every((v) => parseAmountInput(v) !== null);
  const left = received && partsValid ? satang(received) - parts.reduce((s, v) => s + satang(parseAmountInput(v)), BigInt(0)) : null;

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/v1/qtc/payments", {
        method: "POST",
        headers: { "Idempotency-Key": idemKey },
        body: {
          customer_id: customerId,
          amount: received,
          received_on: receivedOn,
          method,
          reference,
          allocations: Object.entries(alloc)
            .filter(([, v]) => v.trim())
            .map(([invoice_id, v]) => ({ invoice_id, amount: parseAmountInput(v) })),
        },
      });
      onDone();
    } catch (err) {
      setError(toApiError(err));
      setBusy(false);
    }
  };

  return (
    <Card className="mb-5">
      <h2 className="mb-3 text-lg font-semibold text-ink">{t("qtc.p.new")}</h2>
      <form onSubmit={save} className="space-y-4">
        {error && <QtcError error={error} />}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={t("qtc.q.customer")} htmlFor="pay-customer">
            <select id="pay-customer" className="input" required value={customerId} onChange={(e) => { setCustomerId(e.target.value); setAlloc({}); }}>
              <option value="">{t("qtc.q.chooseCustomer")}</option>
              {(customers.data?.items ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.code} · {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("qtc.p.amount")} htmlFor="pay-amount" error={amount && !received ? t("qtc.priceList.invalidPrice") : null}>
            <input id="pay-amount" className="input text-right tabular-nums" inputMode="decimal" required value={amount} onChange={(e) => setAmount(e.target.value)} />
          </Field>
          <Field label={t("qtc.p.receivedOn")} htmlFor="pay-date">
            <input id="pay-date" type="date" className="input" required value={receivedOn} max={new Date().toISOString().slice(0, 10)} onChange={(e) => setReceivedOn(e.target.value)} />
          </Field>
          <Field label={t("qtc.p.method")} htmlFor="pay-method">
            <select id="pay-method" className="input" value={method} onChange={(e) => setMethod(e.target.value as typeof method)}>
              {METHODS.map((m) => (
                <option key={m} value={m}>
                  {t(`qtc.p.method.${m}` as MessageKey)}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t("qtc.p.reference")} htmlFor="pay-ref">
            <input id="pay-ref" className="input" maxLength={120} value={reference} onChange={(e) => setReference(e.target.value)} />
          </Field>
        </div>
        {customerId && (
          <fieldset className="rounded-xl border border-line p-3">
            <legend className="px-1 text-sm font-semibold text-ink">{t("qtc.p.allocate")}</legend>
            {open.data && open.data.items.length === 0 && <p className="text-sm text-dim">{t("qtc.p.noOpen")}</p>}
            <ul className="space-y-2">
              {(open.data?.items ?? []).map((inv) => (
                <li key={inv.id} className="flex flex-wrap items-center gap-3 text-sm">
                  <label htmlFor={`alloc-${inv.id}`} className="min-w-0 flex-1 text-ink">
                    {inv.number} · {t("qtc.i.balance")} ฿{formatAmount(inv.balance)}
                  </label>
                  <input
                    id={`alloc-${inv.id}`}
                    className="input w-36 text-right tabular-nums"
                    inputMode="decimal"
                    placeholder={inv.id === initialInvoice ? formatAmount(inv.balance) : "0.00"}
                    value={alloc[inv.id] ?? ""}
                    onChange={(e) => setAlloc({ ...alloc, [inv.id]: e.target.value })}
                  />
                  <Button size="sm" onClick={() => setAlloc({ ...alloc, [inv.id]: inv.balance })}>
                    {formatAmount(inv.balance)}
                  </Button>
                </li>
              ))}
            </ul>
            {left !== null && (
              <p role="status" className={cx("mt-2 text-sm", left < 0 ? "text-bad" : left > 0 ? "text-warn" : "text-ok")}>
                {left < 0 ? t("qtc.p.overAllocated") : left > 0 ? t("qtc.p.unallocated", { amount: formatAmount(toBaht(left)) }) : "✓"}
              </p>
            )}
          </fieldset>
        )}
        <div className="flex gap-2">
          <Button type="submit" variant="primary" loading={busy} disabled={!customerId || !received || !partsValid || (left !== null && left < 0)}>
            {t("qtc.p.save")}
          </Button>
          <Button onClick={onCancel}>{t("qtc.cancel")}</Button>
        </div>
      </form>
    </Card>
  );
}
