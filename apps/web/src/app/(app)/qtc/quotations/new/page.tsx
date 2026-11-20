"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { LineEditor, linesValid } from "@/components/qtc/QuotationParts";
import { Field, QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Button, Card, LoadingBlock } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { type ApiError, api, newIdempotencyKey, toApiError } from "@/lib/api";
import { type Customer, type LineDraft, type Page, type PriceList, type Quotation, type Service, linesBody } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

interface Setup {
  customers: Customer[];
  services: Service[];
  hasPriceList: boolean;
}

/** Every line has a price: typed by a person, or from the approved price list for its service. */
function allPriced(lines: LineDraft[], services: Service[]): boolean {
  const listed = new Map(services.map((x) => [x.id, x.default_price]));
  return lines.length > 0 && lines.every((l) => l.unit_price.trim() !== "" || Boolean(l.service_id && listed.get(l.service_id)));
}

export default function NewQuotationPage() {
  const { t } = useI18n();
  const router = useRouter();
  const setup = useApi<Setup>("qtc:new-quotation", async (signal) => {
    const [customers, services, lists] = await Promise.all([
      api<Page<Customer>>("/v1/qtc/customers", { query: { limit: 100 }, signal }),
      api<Page<Service>>("/v1/qtc/services", { query: { limit: 200 }, signal }),
      api<Page<PriceList>>("/v1/qtc/price-lists", { query: { status: "active", limit: 5 }, signal }),
    ]);
    return { customers: customers.items, services: services.items, hasPriceList: lists.items.some((p) => p.is_default) };
  });
  const [customerId, setCustomerId] = useState("");
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [lines, setLines] = useState<LineDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [idemKey] = useState(newIdempotencyKey);

  const [busyAction, setBusyAction] = useState<"draft" | "submit" | null>(null);

  // "Submit for approval" creates the draft and submits it in one step. The idempotency key makes a retry after
  // a failed submit reuse the same draft instead of creating a second quotation.
  const save = async (submit: boolean) => {
    setBusy(true);
    setBusyAction(submit ? "submit" : "draft");
    setError(null);
    try {
      const body: Record<string, unknown> = { customer_id: customerId, title, brief };
      if (lines.length) body.lines = linesBody(lines);
      const q = await api<Quotation>("/v1/qtc/quotations", { method: "POST", body, headers: { "Idempotency-Key": idemKey } });
      if (submit) await api<Quotation>(`/v1/qtc/quotations/${q.id}/submit`, { method: "POST" });
      router.push(`/qtc/quotations/${q.id}`);
    } catch (err) {
      setError(toApiError(err));
      setBusy(false);
      setBusyAction(null);
    }
  };
  const create = (e: React.FormEvent) => {
    e.preventDefault();
    void save(false);
  };

  const s = setup.data;
  return (
    <QtcShell>
      {setup.error && !s ? (
        <QtcError error={setup.error} onRetry={setup.reload} />
      ) : !s ? (
        <LoadingBlock />
      ) : (
        <Card>
          <h2 className="mb-4 text-lg font-semibold text-ink">{t("qtc.q.new")}</h2>
          {!s.hasPriceList && (
            <p role="alert" className="mb-4 rounded-xl border border-warn/50 bg-warn/10 p-3 text-sm text-ink">
              {t("qtc.q.noPriceList")}
            </p>
          )}
          <form onSubmit={create} className="space-y-4" aria-busy={busy || undefined}>
            {error && <QtcError error={error} />}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("qtc.q.customer")} htmlFor="q-customer">
                <select id="q-customer" className="input" required value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
                  <option value="">{t("qtc.q.chooseCustomer")}</option>
                  {s.customers.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.code} · {c.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("qtc.q.title")} htmlFor="q-title">
                <input id="q-title" className="input" maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} />
              </Field>
            </div>
            <Field label={t("qtc.q.brief")} hint={t("qtc.q.briefHint")} htmlFor="q-brief">
              <textarea id="q-brief" className="input" rows={3} maxLength={4000} value={brief} onChange={(e) => setBrief(e.target.value)} />
            </Field>
            <div>
              <h3 className="mb-1 font-semibold text-ink">{t("qtc.q.lines")}</h3>
              <p className="mb-2 text-xs text-dim">{t("qtc.q.priceHint")}</p>
              <LineEditor lines={lines} onChange={setLines} services={s.services} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" loading={busyAction === "draft"} disabled={busy || !customerId || !s.hasPriceList || !linesValid(lines)}>
                {t("qtc.q.create")}
              </Button>
              <Button
                type="button"
                variant="primary"
                loading={busyAction === "submit"}
                disabled={busy || !customerId || !s.hasPriceList || !linesValid(lines) || !allPriced(lines, s.services)}
                onClick={() => void save(true)}
              >
                {t("qtc.q.submit")}
              </Button>
              {customerId && lines.length > 0 && linesValid(lines) && !allPriced(lines, s.services) && (
                <p className="text-xs text-warn">{t("qtc.q.submitNeedsPrices")}</p>
              )}
            </div>
          </form>
        </Card>
      )}
    </QtcShell>
  );
}
