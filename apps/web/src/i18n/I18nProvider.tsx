"use client";

import { createContext, useContext, useEffect, useMemo, useSyncExternalStore } from "react";
import { DEFAULT_LOCALE, dictionaries, type Locale, type MessageKey } from "./dictionaries";

const COOKIE = "mcr_locale";
const EVENT = "mcr-locale-change";

function readLocale(): Locale {
  const m = document.cookie.match(/(?:^|;\s*)mcr_locale=(th|en)(?:;|$)/);
  return m ? (m[1] as Locale) : DEFAULT_LOCALE;
}

function writeLocale(locale: Locale): void {
  // UI preference only (not sensitive); readable by script on purpose.
  document.cookie = `${COOKIE}=${locale}; path=/; max-age=31536000; samesite=lax`;
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  return () => window.removeEventListener(EVENT, onChange);
}

type Vars = Record<string, string | number>;

export interface I18n {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: MessageKey, vars?: Vars) => string;
  /** For values that come from the API: translates `${prefix}.${value}`, or returns the raw value. */
  tx: (prefix: string, value: string | null | undefined) => string;
  money: (usd: number | null | undefined) => string;
  number: (n: number) => string;
  dateTime: (iso: string | null | undefined) => string;
  time: (iso: string) => string;
  date: (isoDate: string) => string;
  bytes: (n: number) => string;
}

const I18nContext = createContext<I18n | null>(null);

function build(locale: Locale): I18n {
  const dict = dictionaries[locale];
  const tag = locale === "th" ? "th-TH" : "en-US";
  const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  const usdSmall = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });
  const num = new Intl.NumberFormat(tag);
  const dt = new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeStyle: "short" });
  const tm = new Intl.DateTimeFormat(tag, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const d = new Intl.DateTimeFormat(tag, { dateStyle: "medium", timeZone: "UTC" });
  const valid = (iso: string) => !Number.isNaN(new Date(iso).getTime());

  return {
    locale,
    setLocale: writeLocale,
    t: (key, vars) => {
      let s: string = dict[key];
      if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v));
      return s;
    },
    tx: (prefix, value) => {
      if (value === null || value === undefined || value === "") return "—";
      return (dict as Record<string, string>)[`${prefix}.${value}`] ?? value;
    },
    money: (v) => {
      if (v === null || v === undefined) return "—";
      // Sub-cent amounts are common for single tasks; keep them visible.
      return v !== 0 && Math.abs(v) < 0.01 ? usdSmall.format(v) : usd.format(v);
    },
    number: (n) => num.format(n),
    dateTime: (iso) => (iso && valid(iso) ? dt.format(new Date(iso)) : "—"),
    time: (iso) => (valid(iso) ? tm.format(new Date(iso)) : iso),
    date: (isoDate) => (valid(isoDate) ? d.format(new Date(isoDate)) : isoDate),
    bytes: (n) => {
      if (n < 1024) return `${num.format(n)} B`;
      const units = ["KB", "MB", "GB"];
      let v = n / 1024;
      let i = 0;
      while (v >= 1024 && i < units.length - 1) {
        v /= 1024;
        i += 1;
      }
      return `${new Intl.NumberFormat(tag, { maximumFractionDigits: 1 }).format(v)} ${units[i]}`;
    },
  };
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  // Server render and hydration use the default (Thai); the saved choice is applied right after.
  const locale = useSyncExternalStore(subscribe, readLocale, () => DEFAULT_LOCALE);
  const value = useMemo(() => build(locale), [locale]);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside <I18nProvider>");
  return ctx;
}
