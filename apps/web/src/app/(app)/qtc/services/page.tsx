"use client";

import { useState } from "react";
import { Field, QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Badge, Button, Card, EmptyState, LoadingBlock, Switch, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { type ApiError, api, toApiError } from "@/lib/api";
import { formatAmount, formatRate, parseAmountInput, type Page, type PriceList, type Service } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

export default function ServicesPage() {
  const { t } = useI18n();
  const services = useApi<Page<Service>>("qtc:services", (signal) => api<Page<Service>>("/v1/qtc/services", { query: { limit: 200 }, signal }));
  const lists = useApi<Page<PriceList>>("qtc:price-lists", (signal) => api<Page<PriceList>>("/v1/qtc/price-lists", { query: { limit: 50 }, signal }));
  const [creating, setCreating] = useState(false);
  const reloadAll = () => {
    services.reload();
    lists.reload();
  };

  return (
    <QtcShell
      actions={
        !creating && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            {t("qtc.services.new")}
          </Button>
        )
      }
    >
      {creating && (
        <Card className="mb-5">
          <h2 className="mb-3 text-lg font-semibold text-ink">{t("qtc.services.new")}</h2>
          <ServiceForm
            onDone={() => {
              setCreating(false);
              reloadAll();
            }}
            onCancel={() => setCreating(false)}
          />
        </Card>
      )}

      {services.error && !services.data ? (
        <QtcError error={services.error} onRetry={services.reload} />
      ) : !services.data ? (
        <LoadingBlock />
      ) : services.data.items.length === 0 ? (
        <EmptyState title={t("qtc.services.empty")} hint={t("qtc.services.emptyHint")} />
      ) : (
        <ul className="mb-8 divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface">
          {services.data.items.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
              <span className="w-24 shrink-0 font-mono text-xs text-dim">{s.code}</span>
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-ink">{s.name}</span>
                <span className="block text-xs text-dim">{[s.name_en, s.category, s.unit].filter(Boolean).join(" · ")}</span>
              </span>
              {s.vat_exempt && <Badge>{t("qtc.service.vatExempt")}</Badge>}
              <span className={cx("text-sm tabular-nums", s.default_price ? "text-ink" : "text-dim")}>
                {s.default_price ? `฿${formatAmount(s.default_price)}` : t("qtc.service.noPrice")}
              </span>
            </li>
          ))}
        </ul>
      )}

      <PriceLists lists={lists} services={services.data?.items ?? []} onChange={reloadAll} />
    </QtcShell>
  );
}

function ServiceForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const { t } = useI18n();
  const [f, setF] = useState({ code: "", name: "", name_en: "", category: "", unit: "งาน", keywords: "", vat_exempt: false });
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<Service>("/v1/qtc/services", {
        method: "POST",
        body: { ...f, keywords: f.keywords.split(",").map((k) => k.trim()).filter(Boolean) },
      });
      onDone();
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="space-y-4">
      {error && <QtcError error={error} />}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("qtc.service.code")} htmlFor="svc-code">
          <input id="svc-code" className="input" required maxLength={32} pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />
        </Field>
        <Field label={t("qtc.service.name")} htmlFor="svc-name">
          <input id="svc-name" className="input" required maxLength={200} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <Field label={t("qtc.service.nameEn")} htmlFor="svc-name-en">
          <input id="svc-name-en" className="input" maxLength={200} value={f.name_en} onChange={(e) => setF({ ...f, name_en: e.target.value })} />
        </Field>
        <Field label={t("qtc.service.category")} htmlFor="svc-cat">
          <input id="svc-cat" className="input" maxLength={80} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} />
        </Field>
        <Field label={t("qtc.service.unit")} htmlFor="svc-unit">
          <input id="svc-unit" className="input" required maxLength={40} value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} />
        </Field>
        <Field label={t("qtc.service.keywords")} hint={t("qtc.service.keywordsHint")} htmlFor="svc-kw">
          <input id="svc-kw" className="input" maxLength={600} value={f.keywords} onChange={(e) => setF({ ...f, keywords: e.target.value })} />
        </Field>
      </div>
      <Switch checked={f.vat_exempt} onChange={(v) => setF({ ...f, vat_exempt: v })} label={t("qtc.service.vatExempt")} />
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={busy} disabled={!f.code.trim() || !f.name.trim()}>
          {t("qtc.save")}
        </Button>
        <Button onClick={onCancel}>{t("qtc.cancel")}</Button>
      </div>
    </form>
  );
}

function PriceLists({
  lists,
  services,
  onChange,
}: {
  lists: { data?: Page<PriceList>; error?: ApiError; reload: () => void };
  services: Service[];
  onChange: () => void;
}) {
  const { t, dateTime } = useI18n();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [form, setForm] = useState({ name: "", vat: "7", inclusive: false });
  const items = lists.data?.items ?? [];
  const selected = items.find((p) => p.id === selectedId) ?? items.find((p) => p.status === "draft") ?? items[0];

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      onChange();
    } catch (e) {
      setError(toApiError(e));
    }
  };
  const vatFraction = (percent: string) => {
    const p = parseAmountInput(percent);
    if (p === null) return null;
    const [i, f = ""] = p.split(".");
    // percent -> fraction on the digits: "7" -> "0.07", "7.5" -> "0.075"
    const digits = `${i}${f}`;
    const scale = f.length + 2;
    const padded = digits.padStart(scale + 1, "0");
    return `${padded.slice(0, padded.length - scale)}.${padded.slice(padded.length - scale)}`;
  };

  return (
    <section aria-labelledby="price-lists-title">
      <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="price-lists-title" className="text-lg font-semibold text-ink">
            {t("qtc.priceLists")}
          </h2>
          <p className="text-sm text-dim">{t("qtc.priceLists.hint")}</p>
        </div>
        {!creating && <Button onClick={() => setCreating(true)}>{t("qtc.priceLists.new")}</Button>}
      </div>
      {error && <QtcError error={error} />}
      {creating && (
        <Card className="mb-4">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const vat_rate = vatFraction(form.vat);
              if (!vat_rate) return;
              void run(async () => {
                const created = await api<PriceList>("/v1/qtc/price-lists", { method: "POST", body: { name: form.name, vat_rate, prices_include_vat: form.inclusive } });
                setSelectedId(created.id);
                setCreating(false);
              });
            }}
            className="grid gap-4 sm:grid-cols-3 sm:items-end"
          >
            <Field label={t("qtc.priceList.name")} htmlFor="pl-name">
              <input id="pl-name" className="input" required maxLength={120} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>
            <Field label={`${t("qtc.priceList.vat")} (%)`} htmlFor="pl-vat">
              <input id="pl-vat" className="input" inputMode="decimal" value={form.vat} onChange={(e) => setForm({ ...form, vat: e.target.value })} />
            </Field>
            <Switch checked={form.inclusive} onChange={(v) => setForm({ ...form, inclusive: v })} label={t("qtc.priceList.inclusive")} />
            <div className="flex gap-2 sm:col-span-3">
              <Button type="submit" variant="primary" disabled={!form.name.trim() || !vatFraction(form.vat)}>
                {t("qtc.save")}
              </Button>
              <Button onClick={() => setCreating(false)}>{t("qtc.cancel")}</Button>
            </div>
          </form>
        </Card>
      )}

      {lists.error && !lists.data ? (
        <QtcError error={lists.error} onRetry={lists.reload} />
      ) : !lists.data ? (
        <LoadingBlock />
      ) : items.length === 0 ? (
        <p className="text-sm text-dim">{t("qtc.priceLists.empty")}</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
          <ul className="space-y-2" role="list">
            {items.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  aria-pressed={selected?.id === p.id}
                  onClick={() => setSelectedId(p.id)}
                  className={cx("w-full rounded-xl border p-3 text-left transition", selected?.id === p.id ? "border-cyan bg-raised" : "border-line bg-surface hover:border-brand-soft/60")}
                >
                  <span className="block font-medium text-ink">{p.name}</span>
                  <span className="mt-1 flex flex-wrap gap-1.5">
                    <Badge tone={p.status === "active" ? "success" : p.status === "draft" ? "warn" : "neutral"}>{t(`qtc.status.${p.status}`)}</Badge>
                    {p.is_default && <Badge tone="info">{t("qtc.priceList.default")}</Badge>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {selected && (
            <Card>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <h3 className="font-semibold text-ink">{selected.name}</h3>
                <span className="text-xs text-dim">
                  VAT {formatRate(selected.vat_rate)} · {t(selected.prices_include_vat ? "qtc.priceList.inclusive" : "qtc.priceList.exclusive")}
                </span>
              </div>
              {selected.approved_by && (
                <p className="mb-3 text-xs text-dim">
                  {t("qtc.priceList.approvedBy", { who: selected.approved_by })} · {dateTime(selected.approved_at)}
                </p>
              )}
              <ul className="divide-y divide-line">
                {services.map((s) => (
                  <PriceRow key={s.id} list={selected} service={s} onRun={run} />
                ))}
              </ul>
              <div className="mt-4 flex flex-wrap gap-2">
                {selected.status === "draft" && (
                  <Button variant="primary" title={t("qtc.priceList.approveHint")} onClick={() => void run(() => api(`/v1/qtc/price-lists/${selected.id}/activate`, { method: "POST" }))}>
                    {t("qtc.priceList.approve")}
                  </Button>
                )}
                <CopyButton list={selected} onRun={run} onCopied={setSelectedId} />
              </div>
              {selected.status === "draft" && <p className="mt-2 text-xs text-dim">{t("qtc.priceList.approveHint")}</p>}
            </Card>
          )}
        </div>
      )}
    </section>
  );
}

function PriceRow({ list, service, onRun }: { list: PriceList; service: Service; onRun: (fn: () => Promise<unknown>) => Promise<void> }) {
  const { t } = useI18n();
  const item = list.items.find((i) => i.service_id === service.id);
  const [value, setValue] = useState(item ? formatAmount(item.unit_price) : "");
  const parsed = parseAmountInput(value);
  const draft = list.status === "draft";
  const id = `price-${list.id}-${service.id}`;
  return (
    <li className="flex flex-wrap items-center gap-3 py-2.5">
      <label htmlFor={id} className="min-w-0 flex-1 text-sm">
        <span className="block text-ink">{service.name}</span>
        <span className="block text-xs text-dim">
          {service.code} · {service.unit}
        </span>
      </label>
      {draft ? (
        <>
          <input
            id={id}
            className="input w-36 text-right tabular-nums"
            inputMode="decimal"
            placeholder="0.00"
            value={value}
            aria-invalid={value !== "" && parsed === null ? true : undefined}
            title={value !== "" && parsed === null ? t("qtc.priceList.invalidPrice") : undefined}
            onChange={(e) => setValue(e.target.value)}
          />
          <Button size="sm" disabled={parsed === null} onClick={() => void onRun(() => api(`/v1/qtc/price-lists/${list.id}/items/${service.id}`, { method: "PUT", body: { unit_price: parsed } }))}>
            {t("qtc.priceList.setPrice")}
          </Button>
          {item && (
            <Button size="sm" variant="ghost" onClick={() => void onRun(() => api(`/v1/qtc/price-lists/${list.id}/items/${service.id}`, { method: "DELETE" }))}>
              {t("qtc.priceList.remove")}
            </Button>
          )}
        </>
      ) : (
        <span className={cx("text-sm tabular-nums", item ? "text-ink" : "text-dim")}>{item ? `฿${formatAmount(item.unit_price)}` : t("qtc.priceList.notPriced")}</span>
      )}
    </li>
  );
}

function CopyButton({ list, onRun, onCopied }: { list: PriceList; onRun: (fn: () => Promise<unknown>) => Promise<void>; onCopied: (id: string) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(`${list.name} (copy)`);
  if (!open) return <Button onClick={() => setOpen(true)}>{t("qtc.priceList.copy")}</Button>;
  return (
    <form
      className="flex flex-wrap items-end gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        void onRun(async () => {
          const copy = await api<PriceList>(`/v1/qtc/price-lists/${list.id}/copy`, { method: "POST", query: { name } });
          onCopied(copy.id);
          setOpen(false);
        });
      }}
    >
      <Field label={t("qtc.priceList.copyName")} htmlFor={`copy-${list.id}`}>
        <input id={`copy-${list.id}`} className="input" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Button type="submit" variant="primary">
        {t("qtc.save")}
      </Button>
      <Button onClick={() => setOpen(false)}>{t("qtc.cancel")}</Button>
    </form>
  );
}
