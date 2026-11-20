"use client";

// What Mira found for a sales or finance command: her answer, the records behind it, a link to the page,
// and (for anything that writes) Confirm / Cancel. Amounts are shown exactly as the server sent them.

import Link from "next/link";
import { Button, buttonClass, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { formatAmount } from "@/lib/qtc";
import type { QtcAnswer } from "@/lib/qtcAssistant";

const FIGURES: [string, MessageKey][] = [
  ["outstanding", "qtcMira.fig.outstanding"],
  ["overdue", "qtcMira.fig.overdue"],
  ["invoiced_this_month", "qtcMira.fig.invoiced"],
  ["received_this_month", "qtcMira.fig.received"],
];

function itemLine(item: Record<string, unknown>): { label: string; amount: string | null; href?: string } {
  const label = [item.number, item.customer, item.title].filter((x) => typeof x === "string" && x).join(" · ");
  const raw = item.balance ?? item.outstanding ?? item.total;
  return { label, amount: typeof raw === "string" ? raw : null, href: typeof item.link === "string" ? item.link : undefined };
}

export function QtcResultCard({
  answer,
  waiting,
  busy,
  onConfirm,
  onCancel,
}: {
  answer: QtcAnswer;
  waiting: boolean; // the proposal still waits for a yes / no
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const { data } = answer;
  const items = (data.items ?? []).slice(0, 8);
  return (
    <section
      aria-label={t("qtcMira.label")}
      className={cx("rounded-2xl border p-3 sm:p-4", waiting ? "border-warn/50 bg-warn/10" : "border-line bg-panel")}
    >
      <p className={cx("text-sm font-semibold", waiting ? "text-warn" : "text-brand")}>
        {waiting ? t("qtcMira.confirmLabel") : t("qtcMira.label")}
      </p>
      <p className="mt-1 whitespace-pre-wrap text-base leading-relaxed text-ink">{answer.reply}</p>

      {data.lines && data.lines.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm">
          {data.lines.map((line, i) => (
            <li key={i} className="flex justify-between gap-3">
              <span className="text-ink">{line.description}</span>
              <span className={cx("tabular-nums", line.approved_price === null ? "font-semibold text-warn" : "text-dim")}>
                {line.approved_price === null ? t("qtcMira.needsPrice") : formatAmount(line.approved_price)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm">
          {items.map((item, i) => {
            const { label, amount, href } = itemLine(item);
            return (
              <li key={i} className="flex justify-between gap-3">
                {href ? (
                  <Link href={href} className="text-brand underline-offset-2 hover:underline">
                    {label}
                  </Link>
                ) : (
                  <span className="text-ink">{label}</span>
                )}
                {amount !== null && <span className="tabular-nums text-dim">{formatAmount(amount)}</span>}
              </li>
            );
          })}
        </ul>
      )}

      {data.figures && (
        <dl className="mt-2 grid grid-cols-2 gap-2 text-sm">
          {FIGURES.map(([key, label]) => (
            <div key={key} className="rounded-xl bg-bg/60 p-2">
              <dt className="text-dim">{t(label)}</dt>
              <dd className="font-semibold tabular-nums text-ink">{formatAmount(String(data.figures?.[key] ?? "0"))}</dd>
            </div>
          ))}
        </dl>
      )}

      {answer.confirm?.action === "draft_reminders" && <p className="mt-2 text-sm text-dim">{t("qtcMira.noSend")}</p>}

      <div className="mt-3 flex flex-wrap gap-2">
        {waiting && (
          <>
            <Button variant="primary" onClick={onConfirm} disabled={busy}>
              {t("qtcMira.confirm")}
            </Button>
            <Button onClick={onCancel} disabled={busy}>
              {t("qtcMira.cancel")}
            </Button>
          </>
        )}
        {data.link && (
          <Link href={data.link} className={buttonClass("secondary", "md")}>
            {t("qtcMira.open")}
          </Link>
        )}
      </div>
    </section>
  );
}
