"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Badge, Button, Card } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { type ApiError, api, toApiError } from "@/lib/api";
import { type InvoiceStatus, type Order, type Payment, percentsTotal } from "@/lib/qtc";
import { Field, QtcError } from "./QtcShell";

type Tone = "neutral" | "info" | "success" | "warn" | "danger";

export function OrderStatusBadge({ status }: { status: Order["status"] }) {
  const { t } = useI18n();
  const tone: Tone = status === "completed" ? "success" : status === "cancelled" ? "neutral" : "info";
  return <Badge tone={tone}>{t(`qtc.o.status.${status}` as MessageKey)}</Badge>;
}

export function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const { t } = useI18n();
  const tone: Record<InvoiceStatus, Tone> = { draft: "neutral", issued: "info", partially_paid: "warn", paid: "success", cancelled: "neutral", overdue: "danger" };
  return <Badge tone={tone[status]}>{t(`qtc.i.status.${status}` as MessageKey)}</Badge>;
}

export function PaymentStatusBadge({ status }: { status: Payment["status"] }) {
  const { t } = useI18n();
  const tone: Tone = status === "confirmed" ? "success" : status === "rejected" ? "danger" : "warn";
  return <Badge tone={tone}>{t(`qtc.p.status.${status}` as MessageKey)}</Badge>;
}

interface Part {
  key: number;
  kind: "deposit" | "installment" | "final";
  description: string;
  percent: string;
}

/** Turns an accepted quotation into a sales order with a billing schedule that adds up to exactly 100 %. */
export function CreateOrder({ quotationId }: { quotationId: string }) {
  const { t } = useI18n();
  const router = useRouter();
  const [parts, setParts] = useState<Part[]>([
    { key: 1, kind: "deposit", description: t("qtc.o.kind.deposit"), percent: "30" },
    { key: 2, kind: "final", description: t("qtc.o.kind.final"), percent: "70" },
  ]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const sum = percentsTotal(parts.map((p) => p.percent));
  const valid = sum === "100.00" && parts.every((p) => p.description.trim());
  const set = (key: number, patch: Partial<Part>) => setParts(parts.map((p) => (p.key === key ? { ...p, ...patch } : p)));

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const order = await api<Order>("/v1/qtc/orders", {
        method: "POST",
        body: { quotation_id: quotationId, schedule: parts.map((p) => ({ kind: p.kind, description: p.description.trim(), percent: p.percent.trim() })) },
      });
      router.push(`/qtc/orders/${order.id}`);
    } catch (e) {
      setError(toApiError(e));
      setBusy(false);
    }
  };

  return (
    <Card>
      <h3 className="font-semibold text-ink">{t("qtc.o.create")}</h3>
      <p className="mb-3 text-xs text-dim">{t("qtc.o.createHint")}</p>
      {error && <QtcError error={error} />}
      <ul className="space-y-2">
        {parts.map((p, i) => (
          <li key={p.key} className="grid grid-cols-[minmax(0,1fr)_4.5rem_auto] items-end gap-2">
            <Field label={`${i + 1}. ${t(`qtc.o.kind.${p.kind}` as MessageKey)}`} htmlFor={`part-${p.key}`}>
              <input id={`part-${p.key}`} className="input" maxLength={300} value={p.description} onChange={(e) => set(p.key, { description: e.target.value })} />
            </Field>
            <Field label={t("qtc.o.percent")} htmlFor={`pct-${p.key}`}>
              <input id={`pct-${p.key}`} className="input text-right tabular-nums" inputMode="decimal" value={p.percent} onChange={(e) => set(p.key, { percent: e.target.value })} />
            </Field>
            <Button variant="ghost" size="sm" disabled={parts.length === 1} onClick={() => setParts(parts.filter((x) => x.key !== p.key))}>
              {t("qtc.q.remove")}
            </Button>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          onClick={() => {
            const next = Math.max(...parts.map((p) => p.key)) + 1;
            const last = parts[parts.length - 1];
            setParts([...parts.slice(0, -1), { key: next, kind: "installment", description: `${t("qtc.o.kind.installment")} ${parts.length}`, percent: "0" }, last]);
          }}
        >
          {t("qtc.o.addPart")}
        </Button>
        <span role="status" className={`text-sm tabular-nums ${sum === "100.00" ? "text-ok" : "text-warn"}`}>
          {sum === null ? t("qtc.o.mustBe100") : t("qtc.o.sum", { pct: sum.replace(/\.00$/, "") })}
        </span>
      </div>
      <Button className="mt-3" variant="primary" loading={busy} disabled={!valid} onClick={() => void create()}>
        {t("qtc.o.create")}
      </Button>
    </Card>
  );
}
