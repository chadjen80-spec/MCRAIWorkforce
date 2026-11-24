"use client";

import { CafeShell } from "@/components/cafe/CafeShell";
import { Badge, Card, ErrorState, LoadingBlock, SectionTitle } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { cafeApi, type CafeFeatures } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";

function Overview({ features }: { features: CafeFeatures }) {
  const { t } = useI18n();
  const seats = useApi(features.modules.devices ? "cafe-overview-seats" : null, (s) => cafeApi.seats(s), { interval: 5000 });
  const items = seats.data?.items ?? [];
  const figures: [MessageKey, number][] = [
    ["cafe.overview.seats", items.length],
    ["cafe.overview.online", items.filter((s) => s.device?.online).length],
    ["cafe.overview.active", items.filter((s) => (s.session?.remaining_seconds ?? 0) > 0).length],
    ["cafe.overview.help", items.reduce((n, s) => n + s.open_assists, 0)],
  ];
  return (
    <div className="space-y-6">
      {seats.loading && <LoadingBlock />}
      {seats.error && <ErrorState error={seats.error} onRetry={seats.reload} />}
      {seats.data && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {figures.map(([key, value]) => (
            <Card key={key} className="p-4">
              <dt className="text-sm text-dim">{t(key)}</dt>
              <dd className="mt-1 text-3xl font-semibold tabular-nums text-ink">{value}</dd>
            </Card>
          ))}
        </dl>
      )}
      <section>
        <SectionTitle>{t("cafe.overview.modules")}</SectionTitle>
        <ul className="flex flex-wrap gap-2">
          {Object.entries(features.modules).map(([module, on]) => (
            <li key={module}>
              <Badge tone={on ? "success" : "neutral"}>
                {module} · {on ? t("cafe.on") : t("cafe.off")}
              </Badge>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export default function CafeOverviewPage() {
  return <CafeShell>{(features) => <Overview features={features} />}</CafeShell>;
}
