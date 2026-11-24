"use client";

// Seat screen (customer UI), shown here as a staff preview. Dark gaming layout with luminous blue accents: seat, time
// left, membership and wallet on top; a compact Mira panel that never covers the game; tabs for games, food, print,
// time packages, orders and help. Only modules the server has switched on show data; the rest say "not enabled".

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { AssistantAvatar, type AvatarMode } from "@/components/AssistantAvatar";
import { CafeShell } from "@/components/cafe/CafeShell";
import { resultText } from "@/components/cafe/DevicesBoard";
import { Button, ErrorState, LoadingBlock, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { newIdempotencyKey, toApiError, type ApiError } from "@/lib/api";
import { cafeApi, formatRemaining, waitForCommand, type CafeCommand, type CafeFeatures, type CafeSeat } from "@/lib/cafe";
import { askCafe, confirmCafe } from "@/lib/cafeAssistant";
import type { QtcAnswer } from "@/lib/qtcAssistant";
import { useApi } from "@/lib/useApi";
import { VoiceError, canListen, listenOnce } from "@/lib/voice";

type Tab = "games" | "food" | "print" | "packages" | "orders";
const TABS: { id: Tab; key: MessageKey; module: keyof CafeFeatures["modules"] }[] = [
  { id: "games", key: "cafe.seatui.games", module: "launcher" },
  { id: "food", key: "cafe.seatui.food", module: "orders" },
  { id: "print", key: "cafe.seatui.print", module: "printing" },
  { id: "packages", key: "cafe.seatui.packages", module: "members" },
  { id: "orders", key: "cafe.seatui.orders", module: "orders" },
];

const glow = "shadow-[0_0_24px_-6px_rgba(34,211,238,0.55)]";

function useCountdown(seconds: number | undefined): number {
  const [state, setState] = useState({ base: seconds ?? 0, at: 0 });
  const [now, setNow] = useState(0);
  if (state.base !== (seconds ?? 0)) setState({ base: seconds ?? 0, at: now });
  useEffect(() => {
    const id = setInterval(() => setNow((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return Math.max(0, state.base - (now - state.at));
}

function SeatScreen({ seatId, features }: { seatId: string; features: CafeFeatures }) {
  const { t, locale } = useI18n();
  const seats = useApi("cafe-seat-screen", (s) => cafeApi.seats(s), { interval: 5000 });
  const apps = useApi("cafe-seat-apps", (s) => cafeApi.apps(s));
  const seat: CafeSeat | undefined = seats.data?.items.find((s) => s.id === seatId);
  const left = useCountdown(seat?.session?.remaining_seconds);
  const [tab, setTab] = useState<Tab>("games");
  const [launch, setLaunch] = useState<{ key: string; cmd?: CafeCommand; error?: ApiError } | null>(null);

  if (seats.loading) return <LoadingBlock />;
  if (seats.error && !seats.data) return <ErrorState error={seats.error} onRetry={seats.reload} />;
  if (!seat) return <ErrorState error={toApiError(new Error("Seat not found"))} />;

  const installed = seat.device?.installed_apps ?? {};
  const doLaunch = async (key: string) => {
    setLaunch({ key });
    try {
      const cmd = await cafeApi.launch(seat.id, key);
      setLaunch({ key, cmd });
      setLaunch({ key, cmd: await waitForCommand(cmd.id) });
    } catch (e) {
      setLaunch({ key, error: toApiError(e) });
    }
  };

  return (
    <div lang={locale} className="overflow-hidden rounded-2xl border border-cyan/30 bg-[#05080f] text-ink">
      <p className="border-b border-line bg-raised px-4 py-2 text-xs text-dim">{t("cafe.seatui.preview")}</p>
      <header className="grid grid-cols-2 gap-px bg-cyan/20 sm:grid-cols-4">
        {[
          [t("cafe.seatui.seat"), seat.label],
          [t("cafe.seatui.left"), seat.session && left > 0 ? formatRemaining(left) : t("cafe.noSession")],
          [t("cafe.seatui.member"), features.modules.members ? t("cafe.seatui.guest") : t("cafe.seatui.na")],
          [t("cafe.seatui.wallet"), features.modules.members ? "—" : t("cafe.seatui.na")],
        ].map(([label, value], i) => (
          <div key={label} className="bg-[#070c17] px-4 py-3">
            <p className="text-[11px] font-semibold tracking-[0.2em] text-cyan/80">{label}</p>
            <p className={cx("mt-1 font-semibold tabular-nums", i < 2 ? "text-2xl text-cyan drop-shadow-[0_0_8px_rgba(34,211,238,0.6)]" : "text-base")}>
              {value}
            </p>
          </div>
        ))}
      </header>

      <div className="grid gap-4 p-4 lg:grid-cols-[1fr_300px]">
        <section>
          <nav className="mb-4 flex flex-wrap gap-2" aria-label={t("cafe.seatui.games")}>
            {TABS.map((x) => (
              <button
                key={x.id}
                type="button"
                onClick={() => setTab(x.id)}
                aria-pressed={tab === x.id}
                className={cx(
                  "rounded-full border px-4 py-1.5 text-sm font-medium transition",
                  tab === x.id ? `border-cyan bg-cyan/15 text-cyan ${glow}` : "border-line text-dim hover:text-ink",
                )}
              >
                {t(x.key)}
              </button>
            ))}
          </nav>
          {!features.modules[TABS.find((x) => x.id === tab)!.module] ? (
            <p className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-dim">{t("cafe.seatui.na")}</p>
          ) : tab === "games" ? (
            <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
              {(apps.data?.items ?? []).filter((a) => a.enabled).map((a) => {
                const ok = installed[a.app_key] === "installed";
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      disabled={!ok || launch?.key === a.app_key && !launch.cmd && !launch.error}
                      onClick={() => doLaunch(a.app_key)}
                      className={cx(
                        "flex h-28 w-full flex-col justify-between rounded-xl border p-3 text-left transition",
                        ok ? `border-brand/50 bg-gradient-to-br from-brand/25 to-cyan/10 hover:border-cyan ${glow}` : "border-line bg-raised opacity-60",
                      )}
                    >
                      <span className="text-xs uppercase tracking-wider text-cyan/80">{a.category}</span>
                      <span className="font-semibold text-ink">{a.name}</span>
                      {!ok && <span className="text-xs text-dim">{t("cafe.seatui.notInstalled")}</span>}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="rounded-xl border border-dashed border-line p-8 text-center text-sm text-dim">{t("cafe.module.off.hint")}</p>
          )}
          {launch && (
            <p role="status" className="mt-3 text-sm">
              {launch.error ? (
                <span className="text-bad">{launch.error.message}</span>
              ) : launch.cmd ? (
                <span className={launch.cmd.status === "succeeded" ? "text-ok" : "text-ink"}>{resultText(t, launch.cmd)}</span>
              ) : (
                <span className="text-dim">{t("cafe.launching")}</span>
              )}
            </p>
          )}
        </section>
        <aside className="space-y-3">
          <MiraPanel seatLabel={seat.label} onDone={seats.reload} />
          {features.modules.assist && <CallStaff seatLabel={seat.label} />}
        </aside>
      </div>
    </div>
  );
}

function MiraPanel({ seatLabel, onDone }: { seatLabel: string; onDone: () => void }) {
  const { t, locale } = useI18n();
  const [text, setText] = useState("");
  const [mode, setMode] = useState<AvatarMode>("idle");
  const [answer, setAnswer] = useState<QtcAnswer | null>(null);
  const [key, setKey] = useState("");

  // The seat screen always speaks for its own seat, so the seat number is added when the customer leaves it out.
  const ask = async (said: string) => {
    if (!said.trim()) return;
    setMode("thinking");
    const withSeat = /เครื่อง|ที่นั่ง|seat/i.test(said) ? said : `${said} ที่เครื่อง ${seatLabel}`;
    const a = await askCafe(withSeat, locale);
    setAnswer(a ?? { intent: null, agent: null, reply: "—", data: {}, confirm: null });
    setKey(newIdempotencyKey());
    setMode("speaking");
  };
  const confirm = async () => {
    if (!answer?.confirm) return;
    setMode("thinking");
    try {
      setAnswer(await confirmCafe(answer.confirm, locale, key));
    } catch (e) {
      setAnswer({ ...answer, confirm: null, reply: toApiError(e).message });
    }
    setMode("speaking");
    onDone();
  };
  const listen = async () => {
    setMode("listening");
    try {
      const heard = await listenOnce(locale === "th" ? "th-TH" : "en-US");
      setText(heard);
      await ask(heard);
    } catch (e) {
      if (!(e instanceof VoiceError)) throw e;
      setMode("idle");
    }
  };

  return (
    <section aria-label="Mira" className={cx("rounded-2xl border border-cyan/40 bg-[#081022] p-3", glow)}>
      <div className="flex items-center gap-3">
        <AssistantAvatar mode={mode} className="size-20 shrink-0" />
        <p className="text-sm leading-snug text-ink" aria-live="polite">
          {answer?.reply ?? t("cafe.seatui.mira")}
        </p>
      </div>
      {answer?.confirm && (
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="primary" onClick={confirm}>
            {t("qtcMira.confirm")}
          </Button>
          <Button size="sm" onClick={() => setAnswer(null)}>
            {t("qtcMira.cancel")}
          </Button>
        </div>
      )}
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(text);
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Mira"
          className="min-w-0 flex-1 rounded-lg border border-line bg-raised px-2 py-1 text-sm text-ink"
        />
        {canListen() && (
          <Button size="sm" onClick={listen} aria-label={t("voice.start")}>
            🎙
          </Button>
        )}
      </form>
    </section>
  );
}

function CallStaff({ seatLabel }: { seatLabel: string }) {
  const { t, locale } = useI18n();
  const [done, setDone] = useState(false);
  const call = async () => {
    const a = await askCafe(`เรียกพนักงานที่เครื่อง ${seatLabel}`, locale);
    if (a?.confirm) {
      await confirmCafe(a.confirm, locale, newIdempotencyKey());
      setDone(true);
    }
  };
  return (
    <Button className="w-full" variant="primary" onClick={call} disabled={done}>
      {done ? t("cafe.seatui.staffCalled") : t("cafe.seatui.callStaff")}
    </Button>
  );
}

export default function SeatScreenPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { t } = useI18n();
  return (
    <CafeShell module="devices">
      {(features) => (
        <>
          <Link href="/cafe/devices" className="mb-3 inline-block text-sm text-brand hover:underline">
            ← {t("cafe.tab.devices")}
          </Link>
          <SeatScreen seatId={id} features={features} />
        </>
      )}
    </CafeShell>
  );
}
