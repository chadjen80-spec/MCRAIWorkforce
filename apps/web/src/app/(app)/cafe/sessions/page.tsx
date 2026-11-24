"use client";

import { useState } from "react";
import { CafeShell } from "@/components/cafe/CafeShell";
import { Badge, Button, EmptyState, ErrorState, LoadingBlock, Switch } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { cafeApi, formatRemaining } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";

function Sessions() {
  const { t } = useI18n();
  const [all, setAll] = useState(false);
  const rows = useApi(`cafe-sessions-${all}`, (s) => cafeApi.sessions(all ? "all" : "active", s), { interval: 5000 });
  return (
    <div className="space-y-4">
      <Switch checked={all} onChange={setAll} label={t("cafe.sessions.all")} />
      {rows.loading && <LoadingBlock />}
      {rows.error && <ErrorState error={rows.error} onRetry={rows.reload} />}
      {rows.data && rows.data.items.length === 0 && <EmptyState title={t("cafe.sessions.empty")} />}
      {rows.data && rows.data.items.length > 0 && (
        <ul className="space-y-2">
          {rows.data.items.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 rounded-xl border border-line bg-panel px-4 py-3 text-sm">
              <span className="font-semibold text-ink">
                {t("cafe.seat")} {s.seat_label}
              </span>
              <Badge tone={s.status === "active" ? "success" : "neutral"}>{s.status}</Badge>
              <span className="tabular-nums text-cyan">
                {s.status === "active" ? t("cafe.remaining", { time: formatRemaining(s.remaining_seconds) }) : ""}
              </span>
              <span className="text-dim">{new Date(s.started_at).toLocaleString()}</span>
              {s.status === "active" && (
                <Button
                  size="sm"
                  className="ml-auto"
                  onClick={async () => {
                    await cafeApi.endSession(s.id);
                    rows.reload();
                  }}
                >
                  {t("cafe.endSession")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CafeSessionsPage() {
  return <CafeShell module="sessions">{() => <Sessions />}</CafeShell>;
}
