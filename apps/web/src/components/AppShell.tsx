"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { api, authRequest } from "@/lib/api";
import { CAFE_NAV_ENABLED } from "@/lib/cafe";
import type { Session } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { Button, cx } from "./ui";

const ICONS = {
  assistant: "M12 3a4.5 4.5 0 0 1 4.5 4.5v2a4.5 4.5 0 0 1-9 0v-2A4.5 4.5 0 0 1 12 3zM4.5 21a7.5 7.5 0 0 1 15 0M19 3v3M17.5 4.5h3",
  dashboard: "M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z",
  workforce: "M13 2 4 14h7l-1 8 9-12h-7z",
  jobs: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
  deliverables: "M3 5h18v14H3zM3 15l5-5 4 4 3-3 6 6M15.5 9.5h.01",
  posters: "M6 2h12v20H6zM9 6h6M9 10h6M8.5 15.5l2-2 2 2 3-3",
  qtc: "M4 4h16v16H4zM8 9h8M8 13h5M14.5 16.5h2M12 2v2M12 20v2",
  cafe: "M3 6h18v11H3zM8 21h8M12 17v4M7 10h2M8 9v2M15 10h.01M17 11h.01",
  settings:
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.8 1.2V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v.1a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
} as const;

const NAV: { href: string; key: MessageKey; icon: keyof typeof ICONS }[] = [
  { href: "/assistant", key: "nav.assistant", icon: "assistant" },
  { href: "/dashboard", key: "nav.dashboard", icon: "dashboard" },
  { href: "/workforce", key: "nav.workforce", icon: "workforce" },
  { href: "/jobs", key: "nav.jobs", icon: "jobs" },
  { href: "/posters", key: "nav.posters", icon: "posters" },
  { href: "/deliverables", key: "nav.deliverables", icon: "deliverables" },
  { href: "/qtc", key: "nav.qtc", icon: "qtc" },
  // MCR Smart Café appears only when the deployment enables it (NEXT_PUBLIC_CAFE_ENABLED=1).
  ...(CAFE_NAV_ENABLED ? [{ href: "/cafe", key: "nav.cafe" as MessageKey, icon: "cafe" as const }] : []),
  { href: "/settings", key: "nav.settings", icon: "settings" },
];

function Icon({ name }: { name: keyof typeof ICONS }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="size-5 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={ICONS[name]} />
    </svg>
  );
}

export function Brand() {
  const { t } = useI18n();
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span
        aria-hidden="true"
        className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-brand to-cyan text-sm font-bold text-[#04121f]"
      >
        M
      </span>
      <span className="min-w-0 leading-tight">
        <span className="block truncate text-sm font-semibold text-ink">{t("app.name")}</span>
        <span className="block truncate text-xs text-dim">{t("app.product")}</span>
      </span>
    </div>
  );
}

export function LanguageToggle() {
  const { t, locale, setLocale } = useI18n();
  return (
    <div role="group" aria-label={t("header.language")} className="flex rounded-lg border border-line bg-raised p-0.5 text-xs font-semibold">
      {(["th", "en"] as const).map((l) => (
        <button
          key={l}
          type="button"
          lang={l}
          aria-pressed={locale === l}
          title={t(`lang.${l}`)}
          onClick={() => setLocale(l)}
          className={cx(
            "min-h-8 rounded-md px-2.5 uppercase transition",
            locale === l ? "bg-brand text-white" : "text-dim hover:text-ink",
          )}
        >
          {l}
        </button>
      ))}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const pathname = usePathname();
  const [loggingOut, setLoggingOut] = useState(false);
  const me = useApi<Session>("me", (signal) => api<Session>("/v1/auth/me", { signal }));

  const isActive = (href: string) => pathname === href || pathname.startsWith(`${href}/`);

  const logout = async () => {
    setLoggingOut(true);
    try {
      await authRequest("/api/auth/logout");
    } catch {
      // Even if the request failed, leaving for /login is the safe outcome.
    }
    // Deliberate full page load: drops every piece of in-memory state from the signed-in session.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  };

  return (
    <div className="min-h-dvh lg:pl-64">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-brand focus:px-3 focus:py-2 focus:text-sm focus:text-white"
      >
        {t("header.skip")}
      </a>

      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col border-r border-line bg-surface lg:flex">
        <div className="flex h-16 items-center border-b border-line px-5">
          <Brand />
        </div>
        <nav aria-label={t("nav.main")} className="flex-1 space-y-1 overflow-y-auto p-3">
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={isActive(item.href) ? "page" : undefined}
              className={cx(
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition",
                isActive(item.href)
                  ? "bg-brand/15 font-semibold text-ink shadow-[inset_2px_0_0_var(--color-cyan)]"
                  : "text-dim hover:bg-raised hover:text-ink",
              )}
            >
              <Icon name={item.icon} />
              {t(item.key)}
            </Link>
          ))}
        </nav>
        <p className="border-t border-line px-5 py-4 text-xs text-dim">{t("app.by")}</p>
      </aside>

      {/* Header */}
      <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-bg/90 px-4 backdrop-blur sm:px-6">
        <div className="min-w-0 flex-1 lg:hidden">
          <Brand />
        </div>
        <div className="hidden min-w-0 flex-1 lg:block">
          {me.data && (
            <p className="truncate text-sm text-dim">
              <span className="font-medium text-ink">{me.data.organization.name}</span>
              <span className="mx-2">·</span>
              {me.data.user.email}
            </p>
          )}
        </div>
        <LanguageToggle />
        <Button size="sm" variant="ghost" loading={loggingOut} onClick={logout} aria-label={t("header.logout")}>
          <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
          </svg>
          <span className="hidden sm:inline">{t("header.logout")}</span>
        </Button>
      </header>

      <main id="main" className="mx-auto w-full max-w-7xl px-4 pb-28 pt-6 sm:px-6 lg:pb-12">
        {children}
      </main>

      {/* Mobile bottom navigation */}
      <nav
        aria-label={t("nav.main")}
        className="fixed inset-x-0 bottom-0 z-30 grid auto-cols-fr grid-flow-col border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] lg:hidden"
      >
        {NAV.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            aria-current={isActive(item.href) ? "page" : undefined}
            className={cx(
              "flex min-h-14 min-w-0 flex-col items-center justify-center gap-0.5 px-1 py-1.5 text-[11px]",
              isActive(item.href) ? "font-semibold text-cyan" : "text-dim",
            )}
          >
            <Icon name={item.icon} />
            <span className="max-w-full truncate">{t(item.key)}</span>
          </Link>
        ))}
      </nav>
    </div>
  );
}
