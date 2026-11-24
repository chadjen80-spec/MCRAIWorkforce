"use client";

// /cafe/devices: every seat with its PC (online from the last heartbeat), the approved apps the PC reports, the active
// session, help requests and recent commands with the PC's real result. Polls every 3 s.

import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingBlock, RefreshError, SectionTitle, buttonClass, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { toApiError, type ApiError } from "@/lib/api";
import { cafeApi, formatRemaining, waitForCommand, type CafeApp, type CafeCommand, type CafeSeat } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";

const STATUS_TONE: Record<CafeCommand["status"], "neutral" | "info" | "success" | "danger" | "warn"> = {
  queued: "neutral",
  delivered: "info",
  succeeded: "success",
  failed: "danger",
  expired: "warn",
};

const KNOWN_RESULTS = new Set(["STARTED", "NOT_ALLOWLISTED", "NOT_INSTALLED", "START_FAILED", "EXPIRED"]);

/** The seat PC's real answer in words; an unexpected code is shown as a failure with the code. */
export function resultText(t: (k: MessageKey) => string, cmd: Pick<CafeCommand, "status" | "result">): string {
  if (cmd.status === "queued" || cmd.status === "delivered") return t("cafe.result.waiting");
  const code = cmd.result?.code ?? "START_FAILED";
  return KNOWN_RESULTS.has(code) ? t(`cafe.result.${code}` as MessageKey) : `${t("cafe.result.START_FAILED")} (${code})`;
}

export function DevicesBoard() {
  const { t } = useI18n();
  const seats = useApi("cafe-seats", (s) => cafeApi.seats(s), { interval: 3000 });
  const apps = useApi("cafe-apps", (s) => cafeApi.apps(s));
  const commands = useApi("cafe-commands", (s) => cafeApi.commands(s), { interval: 3000 });
  const assists = useApi("cafe-assists", (s) => cafeApi.assists(s), { interval: 5000 });
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const addSeat = async () => {
    setBusy(true);
    setError(null);
    try {
      await cafeApi.createSeat(label.trim(), "");
      setLabel("");
      seats.reload();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  };

  if (seats.loading) return <LoadingBlock />;
  if (!seats.data) return seats.error ? <ErrorState error={seats.error} onRetry={seats.reload} /> : null;
  const refresh = () => {
    seats.reload();
    commands.reload();
    assists.reload();
  };

  return (
    <div className="space-y-6">
      {seats.error && <RefreshError error={seats.error} onRetry={seats.reload} />}
      {error && <ErrorState error={error} compact />}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (label.trim()) void addSeat();
        }}
      >
        <label className="text-sm text-dim">
          {t("cafe.seatLabel")}
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            pattern="[A-Za-z0-9\-]+"
            maxLength={32}
            className="mt-1 block w-32 rounded-lg border border-line bg-raised px-3 py-2 text-ink"
          />
        </label>
        <Button type="submit" loading={busy} disabled={!label.trim()}>
          {t("cafe.addSeat")}
        </Button>
      </form>

      {seats.data.items.length === 0 ? (
        <EmptyState title={t("cafe.empty")} hint={t("cafe.emptyHint")} />
      ) : (
        <ul className="grid gap-4 lg:grid-cols-2">
          {seats.data.items.map((seat) => (
            <li key={seat.id}>
              <SeatCard seat={seat} apps={apps.data?.items ?? []} onChanged={refresh} />
            </li>
          ))}
        </ul>
      )}

      <section>
        <SectionTitle>{t("cafe.help")}</SectionTitle>
        {(assists.data?.items.length ?? 0) === 0 ? (
          <p className="text-sm text-dim">{t("cafe.noHelp")}</p>
        ) : (
          <ul className="space-y-2">
            {assists.data!.items.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-3 rounded-xl border border-warn/40 bg-warn/10 px-3 py-2 text-sm">
                <span className="text-ink">
                  {t("cafe.seat")} {a.seat_label} · {a.source}
                  {a.message ? ` · ${a.message}` : ""}
                </span>
                <Button
                  size="sm"
                  onClick={async () => {
                    await cafeApi.resolveAssist(a.id);
                    refresh();
                  }}
                >
                  {t("cafe.resolve")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle>{t("cafe.commands")}</SectionTitle>
        {(commands.data?.items.length ?? 0) === 0 ? (
          <p className="text-sm text-dim">{t("cafe.noCommands")}</p>
        ) : (
          <ul className="space-y-1 text-sm" aria-label={t("cafe.commands")}>
            {commands.data!.items.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-2 rounded-lg bg-raised px-3 py-2">
                <Badge tone={STATUS_TONE[c.status]}>{t(`cafe.cmd.${c.status}` as MessageKey)}</Badge>
                <span className="text-ink">
                  {t("cafe.seat")} {c.seat_label} · {String(c.args.app_id ?? c.type)}
                </span>
                <span className="text-dim">{resultText(t, c)}</span>
                <span className="ml-auto text-xs text-dim">
                  {c.via} · {c.requested_by} · {new Date(c.created_at).toLocaleTimeString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export function SeatCard({ seat, apps, onChanged }: { seat: CafeSeat; apps: CafeApp[]; onChanged: () => void }) {
  const { t } = useI18n();
  const [code, setCode] = useState<{ code: string; expires_at: string } | null>(null);
  const [appKey, setAppKey] = useState("");
  const [minutes, setMinutes] = useState(60);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [last, setLast] = useState<CafeCommand | null>(null);
  const device = seat.device;
  const installed = Object.entries(device?.installed_apps ?? {});

  const run = async (what: string, fn: () => Promise<unknown>) => {
    setBusy(what);
    setError(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-lg font-semibold text-ink">
          {t("cafe.seat")} {seat.label}
        </h3>
        {device ? (
          <Badge tone={device.online ? "success" : "danger"}>{device.online ? t("cafe.online") : t("cafe.offline")}</Badge>
        ) : (
          <Badge>{t("cafe.noDevice")}</Badge>
        )}
        {seat.open_assists > 0 && <Badge tone="warn">{t("cafe.helpCount", { n: seat.open_assists })}</Badge>}
        <Link href={`/cafe/seat/${seat.id}`} className={cx(buttonClass("ghost", "sm"), "ml-auto")}>
          {t("cafe.seatui.open")}
        </Link>
      </div>

      {device && (
        <p className="mt-1 text-xs text-dim">
          {device.name} · {t("cafe.version", { version: device.client_version || "?" })} ·{" "}
          {t("cafe.lastSeen", { time: device.last_heartbeat_at ? new Date(device.last_heartbeat_at).toLocaleTimeString() : t("cafe.never") })}
        </p>
      )}

      <p className="mt-3 text-sm">
        <span className="text-dim">{t("cafe.session")}: </span>
        {seat.session ? (
          <span className={cx("font-semibold tabular-nums", seat.session.remaining_seconds > 0 ? "text-cyan" : "text-warn")}>
            {seat.session.remaining_seconds > 0 ? t("cafe.remaining", { time: formatRemaining(seat.session.remaining_seconds) }) : t("cafe.timeUp")}
          </span>
        ) : (
          <span className="text-ink">{t("cafe.noSession")}</span>
        )}
      </p>

      {device && (
        <div className="mt-2 text-sm">
          <p className="text-dim">{t("cafe.apps")}</p>
          {installed.length === 0 ? (
            <p className="text-ink">{t("cafe.noApps")}</p>
          ) : (
            <ul className="mt-1 flex flex-wrap gap-1">
              {installed.map(([key, state]) => (
                <li key={key}>
                  <Badge tone={state === "installed" ? "info" : "neutral"}>
                    {key} · {state === "installed" ? t("cafe.appInstalled") : t("cafe.appMissing")}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-end gap-2">
        {seat.session && seat.session.remaining_seconds > 0 ? (
          <Button size="sm" loading={busy === "end"} onClick={() => run("end", () => cafeApi.endSession(seat.session!.id))}>
            {t("cafe.endSession")}
          </Button>
        ) : (
          <>
            <label className="text-xs text-dim">
              {t("cafe.minutes")}
              <input
                type="number"
                min={1}
                max={1440}
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
                className="mt-1 block w-20 rounded-lg border border-line bg-raised px-2 py-1 text-ink"
              />
            </label>
            <Button size="sm" loading={busy === "start"} onClick={() => run("start", () => cafeApi.startSession(seat.id, minutes))}>
              {t("cafe.startSession")}
            </Button>
          </>
        )}
        {device && (
          <>
            <label className="text-xs text-dim">
              {t("cafe.launchPick")}
              <select
                value={appKey}
                onChange={(e) => setAppKey(e.target.value)}
                className="mt-1 block rounded-lg border border-line bg-raised px-2 py-1 text-ink"
              >
                <option value="">—</option>
                {apps.filter((a) => a.enabled).map((a) => (
                  <option key={a.app_key} value={a.app_key}>
                    {a.name}
                  </option>
                ))}
              </select>
            </label>
            <Button
              size="sm"
              variant="primary"
              disabled={!appKey}
              loading={busy === "launch"}
              onClick={() =>
                run("launch", async () => {
                  const cmd = await cafeApi.launch(seat.id, appKey);
                  setLast(cmd);
                  setLast(await waitForCommand(cmd.id));
                })
              }
            >
              {busy === "launch" ? t("cafe.launching") : t("cafe.launch")}
            </Button>
          </>
        )}
        {!device && (
          <Button size="sm" loading={busy === "code"} onClick={() => run("code", async () => setCode(await cafeApi.enrollmentCode(seat.id)))}>
            {t("cafe.enrol")}
          </Button>
        )}
      </div>

      {last && (
        <p role="status" className="mt-2 text-sm">
          <Badge tone={STATUS_TONE[last.status]}>{t(`cafe.cmd.${last.status}` as MessageKey)}</Badge>{" "}
          <span className="text-ink">{resultText(t, last)}</span>
        </p>
      )}
      {code && (
        <p className="mt-2 rounded-lg border border-cyan/40 bg-cyan/10 p-2 font-mono text-xs text-ink">
          {t("cafe.enrolCode", { code: code.code, seat: seat.label, time: new Date(code.expires_at).toLocaleTimeString() })}
        </p>
      )}
      {error && (
        <div className="mt-2">
          <ErrorState error={error} compact />
        </div>
      )}
    </Card>
  );
}
