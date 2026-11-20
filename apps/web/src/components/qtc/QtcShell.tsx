"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { ErrorState, PageHeader, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import type { ApiError } from "@/lib/api";

// Only sections that exist are linked; later phases (orders, invoices, payments) are added
// here as they are built.
const TABS: { href: string; key: MessageKey }[] = [
  { href: "/qtc", key: "qtc.tab.overview" },
  { href: "/qtc/quotations", key: "qtc.tab.quotations" },
  { href: "/qtc/customers", key: "qtc.tab.customers" },
  { href: "/qtc/services", key: "qtc.tab.services" },
];

export function QtcShell({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  const { t, locale } = useI18n();
  const pathname = usePathname();
  const active = (href: string) => (href === "/qtc" ? pathname === "/qtc" : pathname === href || pathname.startsWith(`${href}/`));
  return (
    <div lang={locale}>
      <PageHeader title={t("qtc.title")} subtitle={t("qtc.subtitle")} actions={actions} />
      <nav aria-label={t("qtc.title")} className="mb-5 flex flex-wrap gap-1 border-b border-line">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active(tab.href) ? "page" : undefined}
            className={cx(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition",
              active(tab.href) ? "border-cyan text-ink" : "border-transparent text-dim hover:text-ink",
            )}
          >
            {t(tab.key)}
          </Link>
        ))}
      </nav>
      {children}
    </div>
  );
}

/** Errors with a clear permission-denied state (403) distinct from other failures. */
export function QtcError({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  const { t } = useI18n();
  if (error.status === 403)
    return (
      <p role="alert" className="rounded-xl border border-warn/50 bg-warn/10 p-4 text-sm leading-relaxed text-ink">
        {t("qtc.denied")}
        {error.message && <span className="mt-1 block text-xs text-dim">{error.message}</span>}
      </p>
    );
  return <ErrorState error={error} onRetry={onRetry} />;
}

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: string; error?: string | null; children: ReactNode; htmlFor: string }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="label">
        {label}
      </label>
      {children}
      {error ? (
        <p role="alert" className="hint text-bad">
          {error}
        </p>
      ) : (
        hint && <p className="hint">{hint}</p>
      )}
    </div>
  );
}
