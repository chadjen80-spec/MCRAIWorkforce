"use client";

import { CafeShell } from "@/components/cafe/CafeShell";
import { Badge, Card, SectionTitle } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";

export default function CafeSettingsPage() {
  const { t } = useI18n();
  return (
    <CafeShell>
      {(features) => (
        <Card className="p-4">
          <SectionTitle>{t("cafe.settings.flags")}</SectionTitle>
          <ul className="space-y-1 text-sm">
            <li className="flex justify-between">
              <code className="font-mono text-ink">CAFE_ENABLED</code>
              <Badge tone={features.enabled ? "success" : "neutral"}>{features.enabled ? t("cafe.on") : t("cafe.off")}</Badge>
            </li>
            {Object.entries(features.modules).map(([module, on]) => (
              <li key={module} className="flex justify-between">
                <code className="font-mono text-ink">{module}</code>
                <Badge tone={on ? "success" : "neutral"}>{on ? t("cafe.on") : t("cafe.off")}</Badge>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-sm text-dim">{t("cafe.settings.note")}</p>
        </Card>
      )}
    </CafeShell>
  );
}
