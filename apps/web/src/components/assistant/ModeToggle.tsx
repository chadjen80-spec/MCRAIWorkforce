"use client";

import Link from "next/link";
import { cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { LayoutMode } from "@/lib/layoutMode";

/** Focus / Work switch. Two toggle buttons in a labelled group; the pressed one is the current layout. */
export function ModeToggle({ mode, onChange }: { mode: LayoutMode; onChange: (mode: LayoutMode) => void }) {
  const { t } = useI18n();
  const options: { value: LayoutMode; label: string; hint: string }[] = [
    { value: "work", label: t("mode.work"), hint: t("mode.workHint") },
    { value: "focus", label: t("mode.focus"), hint: t("mode.focusHint") },
  ];
  return (
    <div role="group" aria-label={t("mode.label")} className="inline-flex rounded-full border border-line bg-surface p-1">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={mode === o.value}
          title={o.hint}
          onClick={() => onChange(o.value)}
          className={cx(
            "min-h-9 rounded-full px-4 text-sm font-medium transition",
            mode === o.value ? "bg-gradient-to-r from-brand to-cyan text-[#04121f]" : "text-dim hover:text-ink",
          )}
        >
          {o.label}
          <span className="sr-only">: {o.hint}</span>
        </button>
      ))}
    </div>
  );
}

/** Focus mode keeps the essentials visible in one line, with a way back to the dashboard. */
export function FocusStrip({ waiting, live, onShowDashboard }: { waiting: number; live: number; onShowDashboard: () => void }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line bg-surface px-4 py-2.5 text-sm">
      <Link href="/jobs?status=awaiting_approval" className={cx("font-medium", waiting > 0 ? "text-warn" : "text-dim")}>
        {t("mode.focusStrip", { waiting: String(waiting), live: String(live) })}
      </Link>
      <button type="button" onClick={onShowDashboard} className="rounded-lg px-2 py-1 font-medium text-brand-soft hover:bg-raised">
        {t("mode.showDashboard")}
      </button>
    </div>
  );
}
