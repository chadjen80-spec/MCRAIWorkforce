"use client";

import Link from "next/link";
import { useState } from "react";
import { CustomerForm, emptyDraft } from "@/components/qtc/CustomerForm";
import { QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Badge, Button, Card, EmptyState, LoadingBlock, Pagination, RefreshError } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { api, newIdempotencyKey } from "@/lib/api";
import type { Customer, Page } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

const LIMIT = 20;

export default function CustomersPage() {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<"active" | "archived" | "all">("active");
  const [offset, setOffset] = useState(0);
  const [creating, setCreating] = useState(false);
  // One key per opened form: a double-click or a retry after a timeout cannot create the customer twice.
  const [idemKey, setIdemKey] = useState(newIdempotencyKey);
  const list = useApi<Page<Customer>>(`qtc:customers:${q}:${status}:${offset}`, (signal) =>
    api<Page<Customer>>("/v1/qtc/customers", { query: { q: q || undefined, status, limit: LIMIT, offset }, signal }),
  );

  const create = async (body: Record<string, unknown>) => {
    await api<Customer>("/v1/qtc/customers", { method: "POST", body, headers: { "Idempotency-Key": idemKey } });
    setCreating(false);
    setIdemKey(newIdempotencyKey());
    list.reload();
  };

  return (
    <QtcShell
      actions={
        !creating && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            {t("qtc.customers.new")}
          </Button>
        )
      }
    >
      {creating && (
        <Card className="mb-5">
          <h2 className="mb-3 text-lg font-semibold text-ink">{t("qtc.customers.new")}</h2>
          <CustomerForm initial={emptyDraft} creating onSubmit={create} onCancel={() => setCreating(false)} />
        </Card>
      )}

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <label htmlFor="customer-search" className="label">
            {t("qtc.search")}
          </label>
          <input
            id="customer-search"
            type="search"
            className="input"
            value={q}
            maxLength={100}
            onChange={(e) => {
              setQ(e.target.value);
              setOffset(0);
            }}
          />
        </div>
        <div>
          <label htmlFor="customer-status" className="label">
            {t("jobs.filter")}
          </label>
          <select
            id="customer-status"
            className="input"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as typeof status);
              setOffset(0);
            }}
          >
            {(["active", "archived", "all"] as const).map((s) => (
              <option key={s} value={s}>
                {t(`qtc.status.${s}`)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {list.error && !list.data ? (
        <QtcError error={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <LoadingBlock />
      ) : list.data.items.length === 0 ? (
        <EmptyState title={q ? t("qtc.customers.noMatch") : t("qtc.customers.empty")} hint={q ? undefined : t("qtc.customers.emptyHint")} />
      ) : (
        <>
          {list.error && <RefreshError error={list.error} onRetry={list.reload} />}
          <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
            {list.data.items.map((c) => {
              const primary = c.contacts.find((x) => x.is_primary) ?? c.contacts[0];
              return (
                <li key={c.id}>
                  <Link href={`/qtc/customers/${c.id}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 transition hover:bg-raised">
                    <span className="w-16 shrink-0 font-mono text-xs text-dim">{c.code}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium text-ink">{c.name}</span>
                      {c.legal_name && <span className="block text-xs text-dim">{c.legal_name}</span>}
                    </span>
                    <span className="text-xs text-dim">{primary ? primary.name : c.email || c.phone}</span>
                    {c.status === "archived" && <Badge>{t("qtc.status.archived")}</Badge>}
                  </Link>
                </li>
              );
            })}
          </ul>
          <Pagination total={list.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </QtcShell>
  );
}
