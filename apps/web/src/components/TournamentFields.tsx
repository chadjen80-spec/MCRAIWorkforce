"use client";

import { cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { fileUrl } from "@/lib/api";
import type { BrandAsset, PosterSpec, ScheduleItem } from "@/lib/types";

type TextField =
  | "subtitle"
  | "prize_label"
  | "prize_amount"
  | "registration_text"
  | "badge_text"
  | "cta_text"
  | "qr_url"
  | "venue_detail";

const TEXT_FIELDS: { key: TextField; max: number; wide?: boolean }[] = [
  { key: "subtitle", max: 90, wide: true },
  { key: "prize_label", max: 40 },
  { key: "prize_amount", max: 24 },
  { key: "registration_text", max: 80, wide: true },
  { key: "badge_text", max: 40 },
  { key: "cta_text", max: 20 },
  { key: "qr_url", max: 300, wide: true },
  { key: "venue_detail", max: 80, wide: true },
];

const EMPTY_ITEM: ScheduleItem = { label: "", big: "", lines: [] };

/** The extra fields of the 4:5 tournament poster: prize, registration, category, schedule card, QR, venue photo. */
function CharacterPicker({
  label,
  assets,
  selected,
  onPick,
}: {
  label: string;
  assets: BrandAsset[];
  selected: string | null | undefined;
  onPick: (id: string | null) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-semibold text-dim">{label}</p>
      {assets.length === 0 ? (
        <p className="text-xs text-dim">{t("poster.noLogos")}</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {assets.map((a) => (
            <button
              key={a.id}
              type="button"
              title={a.name}
              aria-pressed={selected === a.id}
              onClick={() => onPick(selected === a.id ? null : a.id)}
              className={cx(
                "h-20 w-16 overflow-hidden rounded-lg border bg-[#0b1322] transition",
                selected === a.id
                  ? "border-cyan ring-2 ring-cyan"
                  : "border-line opacity-75 hover:opacity-100",
              )}
            >
              {/* eslint-disable-next-line @next/next/no-img-element -- signed, expiring asset URL */}
              <img
                src={fileUrl(a.url)}
                alt={a.name}
                className="size-full object-cover object-top"
              />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function TournamentFields({
  spec,
  onChange,
  artworks,
  library = [],
}: {
  spec: PosterSpec;
  onChange: (patch: Partial<PosterSpec>) => void;
  artworks: BrandAsset[];
  /** Every logo and artwork: characters may be cut-out PNGs (logos) or pictures (artwork). */
  library?: BrandAsset[];
}) {
  const { t } = useI18n();
  const category = [
    spec.category_lines?.[0] ?? "",
    spec.category_lines?.[1] ?? "",
  ];
  const schedule = [
    spec.schedule?.[0] ?? EMPTY_ITEM,
    spec.schedule?.[1] ?? EMPTY_ITEM,
  ];
  const setItem = (i: number, patch: Partial<ScheduleItem>) => {
    const next = schedule.map((it, j) => (j === i ? { ...it, ...patch } : it));
    onChange({ schedule: next });
  };

  return (
    <div className="space-y-5">
      <fieldset className="space-y-2">
        <legend className="label">{t("tournament.characters")}</legend>
        <p className="hint">{t("tournament.charactersHint")}</p>
        <div className="grid gap-4 sm:grid-cols-2">
          <CharacterPicker
            label={t("tournament.heroLeft")}
            assets={library}
            selected={spec.hero_left_id}
            onPick={(id) => onChange({ hero_left_id: id })}
          />
          <CharacterPicker
            label={t("tournament.heroRight")}
            assets={library}
            selected={spec.hero_right_id}
            onPick={(id) => onChange({ hero_right_id: id })}
          />
        </div>
      </fieldset>
      <div className="grid gap-4 sm:grid-cols-2">
        {TEXT_FIELDS.map((f) => (
          <div
            key={f.key}
            className={cx("space-y-1.5", f.wide && "sm:col-span-2")}
          >
            <label htmlFor={`poster-${f.key}`} className="label">
              {t(`tournament.${f.key}` as MessageKey)}
            </label>
            <input
              id={`poster-${f.key}`}
              type={f.key === "qr_url" ? "url" : "text"}
              inputMode={f.key === "qr_url" ? "url" : undefined}
              value={spec[f.key] ?? ""}
              maxLength={f.max}
              onChange={(e) => onChange({ [f.key]: e.target.value })}
              placeholder={t(`tournament.${f.key}Ph` as MessageKey)}
              className="input"
            />
          </div>
        ))}
      </div>

      <fieldset className="space-y-2">
        <legend className="label">{t("tournament.category")}</legend>
        <p className="hint">{t("tournament.categoryHint")}</p>
        {category.map((line, i) => (
          <input
            key={i}
            aria-label={`${t("tournament.category")} ${i + 1}`}
            value={line}
            maxLength={60}
            onChange={(e) =>
              onChange({
                category_lines: category.map((c, j) =>
                  j === i ? e.target.value : c,
                ),
              })
            }
            placeholder={t(
              i === 0 ? "tournament.categoryPh1" : "tournament.categoryPh2",
            )}
            className="input"
          />
        ))}
      </fieldset>

      <fieldset className="space-y-3">
        <legend className="label">{t("tournament.schedule")}</legend>
        <p className="hint">{t("tournament.scheduleHint")}</p>
        <div className="grid gap-4 sm:grid-cols-2">
          {schedule.map((item, i) => (
            <div
              key={i}
              className="space-y-2 rounded-lg border border-line bg-bg p-3"
            >
              <p className="text-xs font-semibold text-dim">
                {t("tournament.column", { n: String(i + 1) })}
              </p>
              <input
                aria-label={`${t("tournament.colLabel")} ${i + 1}`}
                value={item.label}
                maxLength={40}
                onChange={(e) => setItem(i, { label: e.target.value })}
                placeholder={t(
                  i === 0 ? "tournament.colLabelPh1" : "tournament.colLabelPh2",
                )}
                className="input"
              />
              <div className="flex gap-2">
                <input
                  aria-label={`${t("tournament.colBig")} ${i + 1}`}
                  value={item.big}
                  maxLength={8}
                  onChange={(e) => setItem(i, { big: e.target.value })}
                  placeholder={i === 0 ? "7" : "8-9"}
                  className="input w-20 shrink-0"
                />
                <div className="min-w-0 flex-1 space-y-2">
                  {[0, 1].map((j) => (
                    <input
                      key={j}
                      aria-label={`${t("tournament.colLine")} ${i + 1}.${j + 1}`}
                      value={item.lines[j] ?? ""}
                      maxLength={24}
                      onChange={(e) => {
                        const lines = [
                          item.lines[0] ?? "",
                          item.lines[1] ?? "",
                        ];
                        lines[j] = e.target.value;
                        setItem(i, { lines });
                      }}
                      placeholder={t(
                        j === 0
                          ? "tournament.colLinePh1"
                          : "tournament.colLinePh2",
                      )}
                      className="input"
                    />
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="label">{t("tournament.venuePhoto")}</legend>
        <p className="hint">{t("tournament.venuePhotoHint")}</p>
        {artworks.length === 0 ? (
          <p className="text-xs text-dim">{t("poster.noArtwork")}</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {artworks.map((a) => (
              <button
                key={a.id}
                type="button"
                title={a.name}
                aria-pressed={spec.venue_photo_id === a.id}
                onClick={() =>
                  onChange({
                    venue_photo_id: spec.venue_photo_id === a.id ? null : a.id,
                  })
                }
                className={cx(
                  "h-14 w-24 overflow-hidden rounded-lg border bg-bg transition",
                  spec.venue_photo_id === a.id
                    ? "border-cyan ring-2 ring-cyan"
                    : "border-line opacity-75 hover:opacity-100",
                )}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- signed, expiring artwork URL */}
                <img
                  src={fileUrl(a.url)}
                  alt={a.name}
                  className="size-full object-cover"
                />
              </button>
            ))}
          </div>
        )}
      </fieldset>
    </div>
  );
}
