"use client";

// Frame for every /cafe page: header, tabs, and the feature-flag gate. A page for a module the API has not enabled
// shows what the module will do instead of data, so nothing unfinished can look live.

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { Card, ErrorState, LoadingBlock, PageHeader, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { cafeApi, type CafeFeatures, type CafeModule } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";

const TABS: { href: string; key: MessageKey }[] = [
  { href: "/cafe", key: "cafe.tab.overview" },
  { href: "/cafe/devices", key: "cafe.tab.devices" },
  { href: "/cafe/sessions", key: "cafe.tab.sessions" },
  { href: "/cafe/games", key: "cafe.tab.games" },
  { href: "/cafe/orders", key: "cafe.tab.orders" },
  { href: "/cafe/printing", key: "cafe.tab.printing" },
  { href: "/cafe/members", key: "cafe.tab.members" },
  { href: "/cafe/reports", key: "cafe.tab.reports" },
  { href: "/cafe/settings", key: "cafe.tab.settings" },
];

export function CafeShell({
  module,
  children,
}: {
  /** The module this page belongs to; omitted for pages that only need the café switched on. */
  module?: CafeModule;
  children: (features: CafeFeatures) => ReactNode;
}) {
  const { t, locale } = useI18n();
  const pathname = usePathname();
  const features = useApi("cafe-features", (signal) => cafeApi.features(signal));
  const active = (href: string) => (href === "/cafe" ? pathname === "/cafe" : pathname === href || pathname.startsWith(`${href}/`));

  let body: ReactNode;
  if (features.loading) body = <LoadingBlock />;
  else if (features.error || !features.data) body = features.error ? <ErrorState error={features.error} onRetry={features.reload} /> : null;
  else if (!features.data.enabled)
    body = (
      <Card className="p-6">
        <p className="font-semibold text-ink">{t("cafe.disabled.title")}</p>
        <p className="mt-1 text-sm text-dim">{t("cafe.disabled.hint")}</p>
      </Card>
    );
  else if (module && !features.data.modules[module]) body = <ModuleOff module={module} />;
  else body = children(features.data);

  return (
    <div lang={locale}>
      <PageHeader title={t("cafe.title")} subtitle={t("cafe.subtitle")} />
      <nav aria-label={t("cafe.title")} className="mb-5 flex flex-wrap gap-1 border-b border-line">
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
      {body}
    </div>
  );
}

const MODULE_TEXT: Partial<Record<CafeModule, MessageKey>> = {
  orders: "cafe.module.orders",
  printing: "cafe.module.printing",
  members: "cafe.module.members",
  reports: "cafe.module.reports",
};

export function ModuleOff({ module }: { module: CafeModule }) {
  const { t } = useI18n();
  const what = MODULE_TEXT[module];
  return (
    <Card className="p-6">
      <p className="font-semibold text-ink">{t("cafe.module.off.title", { module })}</p>
      {what && <p className="mt-2 text-sm text-ink">{t(what)}</p>}
      <p className="mt-2 text-sm text-dim">{t("cafe.module.off.hint")}</p>
    </Card>
  );
}
