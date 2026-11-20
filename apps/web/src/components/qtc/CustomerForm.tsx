"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { type ApiError, toApiError } from "@/lib/api";
import { type Customer, thaiTaxIdValid } from "@/lib/qtc";
import { Field, QtcError } from "./QtcShell";

export interface CustomerDraft {
  code: string;
  name: string;
  legal_name: string;
  customer_type: "company" | "individual";
  tax_id: string;
  branch: string;
  address: string;
  email: string;
  phone: string;
  payment_terms_days: string;
  notes: string;
}

export const emptyDraft: CustomerDraft = {
  code: "",
  name: "",
  legal_name: "",
  customer_type: "company",
  tax_id: "",
  branch: "00000",
  address: "",
  email: "",
  phone: "",
  payment_terms_days: "30",
  notes: "",
};

export const draftOf = (c: Customer): CustomerDraft => ({
  code: c.code,
  name: c.name,
  legal_name: c.legal_name,
  customer_type: c.customer_type,
  tax_id: c.tax_id ?? "",
  branch: c.branch,
  address: c.address,
  email: c.email,
  phone: c.phone,
  payment_terms_days: String(c.payment_terms_days),
  notes: c.notes,
});

/** The request body for a draft: empty optional fields are left out (code) or cleared (tax ID). */
export function customerBody(d: CustomerDraft, { creating }: { creating: boolean }): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: d.name.trim(),
    legal_name: d.legal_name.trim(),
    customer_type: d.customer_type,
    tax_id: d.tax_id.trim() || null,
    branch: d.branch.trim() || "00000",
    address: d.address,
    email: d.email.trim(),
    phone: d.phone.trim(),
    payment_terms_days: Number.parseInt(d.payment_terms_days, 10) || 0,
    notes: d.notes,
  };
  if (d.code.trim() || !creating) body.code = d.code.trim() || undefined;
  return body;
}

export function CustomerForm({
  initial,
  creating,
  onSubmit,
  onCancel,
}: {
  initial: CustomerDraft;
  creating: boolean;
  onSubmit: (body: Record<string, unknown>) => Promise<void>;
  onCancel?: () => void;
}) {
  const { t } = useI18n();
  const [d, setD] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const set = (k: keyof CustomerDraft) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setD((v) => ({ ...v, [k]: e.target.value }));
  const taxBad = d.tax_id.trim() !== "" && !thaiTaxIdValid(d.tax_id);
  const id = creating ? "new-customer" : "edit-customer";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (taxBad || !d.name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(customerBody(d, { creating }));
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4" aria-busy={busy || undefined}>
      {error && <QtcError error={error} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("qtc.customer.name")} htmlFor={`${id}-name`}>
          <input id={`${id}-name`} className="input" required maxLength={200} value={d.name} onChange={set("name")} />
        </Field>
        <Field label={t("qtc.customer.code")} hint={creating ? t("qtc.customer.codeHint") : undefined} htmlFor={`${id}-code`}>
          <input id={`${id}-code`} className="input" maxLength={32} value={d.code} onChange={set("code")} />
        </Field>
        <Field label={t("qtc.customer.legalName")} htmlFor={`${id}-legal`}>
          <input id={`${id}-legal`} className="input" maxLength={300} value={d.legal_name} onChange={set("legal_name")} />
        </Field>
        <Field label={t("qtc.customer.type")} htmlFor={`${id}-type`}>
          <select id={`${id}-type`} className="input" value={d.customer_type} onChange={set("customer_type")}>
            <option value="company">{t("qtc.customer.type.company")}</option>
            <option value="individual">{t("qtc.customer.type.individual")}</option>
          </select>
        </Field>
        <Field label={t("qtc.customer.taxId")} error={taxBad ? t("qtc.customer.taxIdInvalid") : null} htmlFor={`${id}-tax`}>
          <input id={`${id}-tax`} className="input" inputMode="numeric" maxLength={17} value={d.tax_id} onChange={set("tax_id")} aria-invalid={taxBad || undefined} />
        </Field>
        <Field label={t("qtc.customer.branch")} htmlFor={`${id}-branch`}>
          <input id={`${id}-branch`} className="input" inputMode="numeric" pattern="\d{5}" maxLength={5} value={d.branch} onChange={set("branch")} />
        </Field>
        <Field label={t("qtc.customer.email")} htmlFor={`${id}-email`}>
          <input id={`${id}-email`} type="email" className="input" maxLength={320} value={d.email} onChange={set("email")} />
        </Field>
        <Field label={t("qtc.customer.phone")} htmlFor={`${id}-phone`}>
          <input id={`${id}-phone`} className="input" maxLength={40} value={d.phone} onChange={set("phone")} />
        </Field>
        <Field label={t("qtc.customer.terms")} htmlFor={`${id}-terms`}>
          <input id={`${id}-terms`} type="number" min={0} max={365} className="input" value={d.payment_terms_days} onChange={set("payment_terms_days")} />
        </Field>
      </div>
      <Field label={t("qtc.customer.address")} htmlFor={`${id}-address`}>
        <textarea id={`${id}-address`} className="input" rows={2} maxLength={2000} value={d.address} onChange={set("address")} />
      </Field>
      <Field label={t("qtc.customer.notes")} htmlFor={`${id}-notes`}>
        <textarea id={`${id}-notes`} className="input" rows={2} maxLength={4000} value={d.notes} onChange={set("notes")} />
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" loading={busy} disabled={taxBad || !d.name.trim()}>
          {t("qtc.save")}
        </Button>
        {onCancel && <Button onClick={onCancel}>{t("qtc.cancel")}</Button>}
      </div>
    </form>
  );
}
