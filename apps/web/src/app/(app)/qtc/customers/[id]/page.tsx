"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { CustomerForm, draftOf } from "@/components/qtc/CustomerForm";
import { Field, QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Badge, Button, Card, LoadingBlock, Switch } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { type ApiError, api, toApiError } from "@/lib/api";
import type { Contact, Customer, Page } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

interface AuditEvent {
  id: string;
  action: string;
  actor: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export default function CustomerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t, dateTime } = useI18n();
  const customer = useApi<Customer>(`qtc:customer:${id}`, (signal) => api<Customer>(`/v1/qtc/customers/${id}`, { signal }));
  // History is for owners and admins; others simply do not see the section.
  const history = useApi<Page<AuditEvent>>(`qtc:customer-history:${id}`, (signal) =>
    api<Page<AuditEvent>>("/v1/qtc/audit-events", { query: { entity_type: "customer", entity_id: id, limit: 20 }, signal }),
  );
  const [saved, setSaved] = useState(false);
  const [statusError, setStatusError] = useState<ApiError | null>(null);
  const c = customer.data;

  const save = async (body: Record<string, unknown>) => {
    setSaved(false);
    await api<Customer>(`/v1/qtc/customers/${id}`, { method: "PATCH", body });
    setSaved(true);
    customer.reload();
    history.reload();
  };
  const toggleArchive = async () => {
    if (!c) return;
    setStatusError(null);
    try {
      await api<Customer>(`/v1/qtc/customers/${id}`, { method: "PATCH", body: { status: c.status === "active" ? "archived" : "active" } });
      customer.reload();
      history.reload();
    } catch (e) {
      setStatusError(toApiError(e));
    }
  };

  return (
    <QtcShell>
      {customer.error && !c ? (
        <QtcError error={customer.error} onRetry={customer.reload} />
      ) : !c ? (
        <LoadingBlock />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
          <Card>
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-ink">{c.name}</h2>
              <span className="font-mono text-xs text-dim">{c.code}</span>
              {c.status === "archived" && <Badge>{t("qtc.status.archived")}</Badge>}
              <Button size="sm" className="ml-auto" onClick={toggleArchive}>
                {c.status === "active" ? t("qtc.archive") : t("qtc.restore")}
              </Button>
            </div>
            {statusError && <QtcError error={statusError} />}
            {saved && (
              <p role="status" className="mb-3 text-sm text-ok">
                {t("qtc.saved")}
              </p>
            )}
            <CustomerForm key={c.updated_at} initial={draftOf(c)} creating={false} onSubmit={save} />
          </Card>
          <div className="space-y-5">
            <Contacts customer={c} onChange={() => { customer.reload(); history.reload(); }} />
            {history.data && history.data.items.length > 0 && (
              <Card>
                <h2 className="mb-2 font-semibold text-ink">{t("qtc.customer.history")}</h2>
                <ol className="space-y-2 text-xs">
                  {history.data.items.map((e) => (
                    <li key={e.id} className="border-l-2 border-line pl-2">
                      <span className="font-medium text-ink">{e.action}</span>
                      <span className="block text-dim">
                        {e.actor} · {dateTime(e.created_at)}
                      </span>
                    </li>
                  ))}
                </ol>
              </Card>
            )}
          </div>
        </div>
      )}
    </QtcShell>
  );
}

function Contacts({ customer, onChange }: { customer: Customer; onChange: () => void }) {
  const { t } = useI18n();
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [form, setForm] = useState({ name: "", title: "", email: "", phone: "", line_id: "", is_primary: false, consent_email: false, consent_line: false });

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api<Contact>(`/v1/qtc/customers/${customer.id}/contacts`, { method: "POST", body: form });
      setAdding(false);
      setForm({ name: "", title: "", email: "", phone: "", line_id: "", is_primary: false, consent_email: false, consent_line: false });
      onChange();
    } catch (err) {
      setError(toApiError(err));
    }
  };
  const patch = async (contact: Contact, body: Partial<Contact>) => {
    setError(null);
    try {
      await api<Contact>(`/v1/qtc/customers/${customer.id}/contacts/${contact.id}`, { method: "PATCH", body });
      onChange();
    } catch (err) {
      setError(toApiError(err));
    }
  };

  return (
    <Card>
      <h2 className="font-semibold text-ink">{t("qtc.customer.contacts")}</h2>
      <p className="mb-3 text-xs text-dim">{t("qtc.contact.consentHint")}</p>
      {error && <QtcError error={error} />}
      {customer.contacts.length === 0 ? (
        <p className="text-sm text-dim">{t("qtc.customer.noContacts")}</p>
      ) : (
        <ul className="space-y-3">
          {customer.contacts.map((x) => (
            <li key={x.id} className="rounded-xl border border-line bg-bg p-3 text-sm">
              <p className="font-medium text-ink">
                {x.name} {x.is_primary && <Badge tone="info">{t("qtc.contact.primary")}</Badge>}
              </p>
              <p className="text-xs text-dim">{[x.title, x.email, x.phone, x.line_id && `LINE ${x.line_id}`].filter(Boolean).join(" · ")}</p>
              <div className="mt-2 space-y-1.5">
                {!x.is_primary && (
                  <Button size="sm" onClick={() => void patch(x, { is_primary: true })}>
                    {t("qtc.contact.primary")}
                  </Button>
                )}
                <Switch checked={x.consent_email} onChange={(v) => void patch(x, { consent_email: v })} label={t("qtc.contact.consentEmail")} />
                <Switch checked={x.consent_line} onChange={(v) => void patch(x, { consent_line: v })} label={t("qtc.contact.consentLine")} />
              </div>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <form onSubmit={add} className="mt-4 space-y-3">
          <Field label={t("qtc.contact.name")} htmlFor="contact-name">
            <input id="contact-name" className="input" required maxLength={200} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </Field>
          <Field label={t("qtc.contact.title")} htmlFor="contact-title">
            <input id="contact-title" className="input" maxLength={120} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </Field>
          <Field label={t("qtc.customer.email")} htmlFor="contact-email">
            <input id="contact-email" type="email" className="input" maxLength={320} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </Field>
          <Field label={t("qtc.customer.phone")} htmlFor="contact-phone">
            <input id="contact-phone" className="input" maxLength={40} value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          </Field>
          <Field label={t("qtc.contact.line")} htmlFor="contact-line">
            <input id="contact-line" className="input" maxLength={80} value={form.line_id} onChange={(e) => setForm({ ...form, line_id: e.target.value })} />
          </Field>
          <Switch checked={form.is_primary} onChange={(v) => setForm({ ...form, is_primary: v })} label={t("qtc.contact.primary")} />
          <Switch checked={form.consent_email} onChange={(v) => setForm({ ...form, consent_email: v })} label={t("qtc.contact.consentEmail")} />
          <Switch checked={form.consent_line} onChange={(v) => setForm({ ...form, consent_line: v })} label={t("qtc.contact.consentLine")} />
          <div className="flex gap-2">
            <Button type="submit" variant="primary" disabled={!form.name.trim()}>
              {t("qtc.save")}
            </Button>
            <Button onClick={() => setAdding(false)}>{t("qtc.cancel")}</Button>
          </div>
        </form>
      ) : (
        <Button className="mt-3" onClick={() => setAdding(true)}>
          {t("qtc.customer.addContact")}
        </Button>
      )}
    </Card>
  );
}
