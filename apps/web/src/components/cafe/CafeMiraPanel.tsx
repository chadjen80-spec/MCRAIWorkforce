"use client";

// Live café view inside Mira's screen (where the agent graph goes): seats, which PCs are online, time left and help
// requests, refreshed every 3 s from the API. Everything shown comes from the server; nothing is assumed.

import { Badge, ErrorState, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { cafeApi, formatRemaining } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";

export function CafeMiraPanel({ fill = false }: { fill?: boolean }) {
  const { t } = useI18n();
  const features = useApi("mira-cafe-features", (s) => cafeApi.features(s));
  const on = features.data?.enabled && features.data.modules.devices;
  const seats = useApi(on ? "mira-cafe-seats" : null, (s) => cafeApi.seats(s), { interval: 3000 });
  const assists = useApi(on && features.data?.modules.assist ? "mira-cafe-assists" : null, (s) => cafeApi.assists(s), { interval: 3000 });

  const box = cx("rounded-xl border border-cyan/30 bg-[#050912] p-4 text-ink", fill && "min-h-0 flex-1 overflow-auto");
  if (features.error) return <ErrorState error={features.error} onRetry={features.reload} />;
  if (features.loading || (on && seats.loading)) {
    return <div className={cx(box, "flex h-64 items-center justify-center text-sm text-dim")}>{t("common.loading")}</div>;
  }
  if (!on) {
    return (
      <div className={box}>
        <p className="font-semibold">{t("cafe.disabled.title")}</p>
        <p className="mt-1 text-sm text-dim">{t("cafe.disabled.hint")}</p>
      </div>
    );
  }
  if (seats.error && !seats.data) return <ErrorState error={seats.error} onRetry={seats.reload} />;

  const items = seats.data?.items ?? [];
  const figures: [MessageKey, number][] = [
    ["cafe.overview.seats", items.length],
    ["cafe.overview.online", items.filter((s) => s.device?.online).length],
    ["cafe.overview.active", items.filter((s) => (s.session?.remaining_seconds ?? 0) > 0).length],
    ["cafe.overview.help", assists.data?.items.length ?? items.reduce((n, s) => n + s.open_assists, 0)],
  ];

  return (
    <div className={box} aria-label={t("cafe.title")}>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {figures.map(([key, value]) => (
          <div key={key} className="rounded-lg border border-line bg-[#070c17] px-3 py-2">
            <dt className="text-xs text-dim">{t(key)}</dt>
            <dd className="mt-0.5 text-2xl font-semibold tabular-nums text-cyan drop-shadow-[0_0_8px_rgba(34,211,238,0.5)]">{value}</dd>
          </div>
        ))}
      </dl>

      {items.length === 0 ? (
        <p className="mt-4 rounded-lg border border-dashed border-line p-6 text-center text-sm text-dim">
          {t("cafe.empty")} · {t("cafe.emptyHint")}
        </p>
      ) : (
        <ul className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
          {items.map((seat) => {
            const left = seat.session?.remaining_seconds ?? 0;
            return (
              <li
                key={seat.id}
                className={cx(
                  "rounded-lg border px-3 py-2",
                  seat.device?.online ? "border-cyan/40 bg-cyan/5" : "border-line bg-[#070c17]",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold">
                    {t("cafe.seat")} {seat.label}
                  </span>
                  {seat.device ? (
                    <Badge tone={seat.device.online ? "success" : "danger"}>{seat.device.online ? t("cafe.online") : t("cafe.offline")}</Badge>
                  ) : (
                    <Badge>{t("cafe.noDevice")}</Badge>
                  )}
                </div>
                <p className={cx("mt-1 text-sm tabular-nums", left > 0 ? "text-cyan" : "text-dim")}>
                  {left > 0 ? t("cafe.remaining", { time: formatRemaining(left) }) : t("cafe.noSession")}
                </p>
                {seat.open_assists > 0 && <p className="mt-1 text-xs text-warn">{t("cafe.helpCount", { n: seat.open_assists })}</p>}
              </li>
            );
          })}
        </ul>
      )}

      {(assists.data?.items.length ?? 0) > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {assists.data!.items.slice(0, 5).map((a) => (
            <li key={a.id} className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-1.5">
              {t("cafe.seat")} {a.seat_label} · {a.source}
              {a.message ? ` · ${a.message}` : ""}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
