"use client";

import { Badge, Button, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { type LineDraft, type Quotation, type QuotationStatus, type Service, formatAmount, formatRate, parseAmountInput, parseQuantity } from "@/lib/qtc";

const TONE: Record<QuotationStatus, "neutral" | "info" | "success" | "warn" | "danger"> = {
  draft: "neutral",
  pending_approval: "warn",
  approved: "info",
  sent: "info",
  accepted: "success",
  declined: "danger",
  cancelled: "neutral",
  expired: "danger",
};

export function QuotationStatusBadge({ status }: { status: QuotationStatus }) {
  const { t } = useI18n();
  return <Badge tone={TONE[status]}>{t(`qtc.q.status.${status}` as MessageKey)}</Badge>;
}

let seq = 0;
export const newLine = (svc?: Service): LineDraft => ({
  key: `new-${++seq}`,
  service_id: svc?.id ?? null,
  description: svc ? svc.name : "",
  unit: svc ? svc.unit : "",
  quantity: "1",
  unit_price: "",
  discount_percent: "0",
});

/** Lines of a draft. Empty price = the approved price list prices it; a typed price is recorded as manual. */
export function LineEditor({ lines, onChange, services }: { lines: LineDraft[]; onChange: (lines: LineDraft[]) => void; services: Service[] }) {
  const { t } = useI18n();
  const byId = new Map(services.map((s) => [s.id, s]));
  const set = (key: string, patch: Partial<LineDraft>) => onChange(lines.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  return (
    <div className="space-y-3">
      {lines.length === 0 && <p className="text-sm text-dim">{t("qtc.q.noLinesHint")}</p>}
      {lines.map((l, i) => {
        const svc = l.service_id ? byId.get(l.service_id) : undefined;
        const priceBad = l.unit_price.trim() !== "" && parseAmountInput(l.unit_price) === null;
        const qtyBad = parseQuantity(l.quantity) === null || parseQuantity(l.quantity) === "0";
        const listed = svc?.default_price;
        const needsPrice = l.unit_price.trim() === "" && !listed;
        return (
          <fieldset key={l.key} className={cx("rounded-xl border p-3", needsPrice ? "border-warn/60" : "border-line")}>
            <legend className="px-1 text-xs text-dim">
              {i + 1}. {svc ? svc.code : t("qtc.q.addCustom")}
            </legend>
            <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_6rem_6rem]">
              <div>
                <label htmlFor={`${l.key}-desc`} className="label">
                  {t("qtc.q.description")}
                </label>
                <input id={`${l.key}-desc`} className="input" maxLength={2000} value={l.description} onChange={(e) => set(l.key, { description: e.target.value })} />
              </div>
              <div>
                <label htmlFor={`${l.key}-qty`} className="label">
                  {t("qtc.q.qty")}
                </label>
                <input id={`${l.key}-qty`} className="input text-right tabular-nums" inputMode="decimal" value={l.quantity} aria-invalid={qtyBad || undefined} onChange={(e) => set(l.key, { quantity: e.target.value })} />
              </div>
              <div>
                <label htmlFor={`${l.key}-unit`} className="label">
                  {t("qtc.q.unit")}
                </label>
                <input id={`${l.key}-unit`} className="input" maxLength={40} value={l.unit} onChange={(e) => set(l.key, { unit: e.target.value })} />
              </div>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_7rem_auto] sm:items-end">
              <div>
                <label htmlFor={`${l.key}-price`} className="label">
                  {t("qtc.q.price")}
                </label>
                <input
                  id={`${l.key}-price`}
                  className="input text-right tabular-nums"
                  inputMode="decimal"
                  placeholder={listed ? formatAmount(listed) : ""}
                  value={l.unit_price}
                  aria-invalid={priceBad || undefined}
                  aria-describedby={`${l.key}-price-note`}
                  onChange={(e) => set(l.key, { unit_price: e.target.value })}
                />
                <p id={`${l.key}-price-note`} className={cx("hint", needsPrice ? "text-warn" : priceBad ? "text-bad" : "")}>
                  {priceBad
                    ? t("qtc.priceList.invalidPrice")
                    : l.unit_price.trim()
                      ? t("qtc.q.source.manual")
                      : listed
                        ? t("qtc.q.fromList", { price: formatAmount(listed) })
                        : t("qtc.q.needsPrice")}
                </p>
              </div>
              <div>
                <label htmlFor={`${l.key}-disc`} className="label">
                  {t("qtc.q.discount")}
                </label>
                <input id={`${l.key}-disc`} className="input text-right tabular-nums" inputMode="decimal" value={l.discount_percent} onChange={(e) => set(l.key, { discount_percent: e.target.value })} />
              </div>
              <Button variant="ghost" onClick={() => onChange(lines.filter((x) => x.key !== l.key))}>
                {t("qtc.q.remove")}
              </Button>
            </div>
          </fieldset>
        );
      })}
      <div className="flex flex-wrap gap-2">
        <label htmlFor="add-service" className="sr-only">
          {t("qtc.q.addService")}
        </label>
        <select
          id="add-service"
          className="input w-auto"
          value=""
          onChange={(e) => {
            const svc = byId.get(e.target.value);
            if (svc) onChange([...lines, newLine(svc)]);
          }}
        >
          <option value="">{t("qtc.q.addService")} …</option>
          {services.map((s) => (
            <option key={s.id} value={s.id}>
              {s.code} · {s.name}
              {s.default_price ? ` · ฿${formatAmount(s.default_price)}` : ""}
            </option>
          ))}
        </select>
        <Button onClick={() => onChange([...lines, newLine()])}>{t("qtc.q.addCustom")}</Button>
      </div>
    </div>
  );
}

export function linesValid(lines: LineDraft[]): boolean {
  return lines.every(
    (l) =>
      (l.service_id || l.description.trim()) &&
      parseQuantity(l.quantity) !== null &&
      parseQuantity(l.quantity) !== "0" &&
      (l.unit_price.trim() === "" || parseAmountInput(l.unit_price) !== null) &&
      parseQuantity(l.discount_percent) !== null,
  );
}

/** Totals as the server calculated them (never recomputed in the browser). */
export function Totals({ q }: { q: Quotation }) {
  const { t } = useI18n();
  return (
    <dl className="ml-auto w-full max-w-sm space-y-1 text-sm">
      <div className="flex justify-between gap-4">
        <dt className="text-dim">{t("qtc.q.subtotal")}</dt>
        <dd className="tabular-nums text-ink">{formatAmount(q.subtotal)}</dd>
      </div>
      {q.discount_total !== "0.00" && (
        <div className="flex justify-between gap-4">
          <dt className="text-dim">{t("qtc.q.discountTotal")}</dt>
          <dd className="tabular-nums text-ink">{formatAmount(q.discount_total)}</dd>
        </div>
      )}
      <div className="flex justify-between gap-4">
        <dt className="text-dim">{t("qtc.q.vat", { rate: formatRate(q.vat_rate) })}</dt>
        <dd className="tabular-nums text-ink">{formatAmount(q.vat_total)}</dd>
      </div>
      <div className="flex justify-between gap-4 border-t border-line pt-1 text-base font-semibold">
        <dt className="text-ink">{t("qtc.q.grandTotal")}</dt>
        <dd className="tabular-nums text-ink">฿{formatAmount(q.grand_total)}</dd>
      </div>
      <p className="text-right text-xs text-dim">({q.amount_in_words})</p>
      {q.prices_include_vat && <p className="text-right text-xs text-dim">{t("qtc.q.inclusive")}</p>}
    </dl>
  );
}
