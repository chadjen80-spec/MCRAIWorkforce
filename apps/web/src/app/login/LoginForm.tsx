"use client";

import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Brand, LanguageToggle } from "@/components/AppShell";
import { Button, ErrorState } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError, authRequest, toApiError } from "@/lib/api";
import { safeNext } from "@/lib/safeNext";
import type { Session } from "@/lib/types";

/** Only same-site relative paths are accepted as a post-login destination. */

export function LoginForm() {
  const { t } = useI18n();
  const search = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await authRequest<Session>("/api/auth/login", { email: email.trim(), password });
      // Full navigation so the new session cookie is used for every request.
      window.location.assign(safeNext(search.get("next")));
    } catch (err) {
      setError(toApiError(err));
      setSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-dvh flex-col items-center justify-center overflow-hidden px-4 py-10">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 left-1/2 size-[520px] -translate-x-1/2 rounded-full bg-brand/20 blur-3xl"
      />
      <div className="relative w-full max-w-sm">
        <div className="mb-6 flex items-center justify-between gap-3">
          <Brand />
          <LanguageToggle />
        </div>
        <main className="rounded-2xl border border-line bg-surface p-6">
          <h1 className="text-xl font-semibold text-ink">{t("login.title")}</h1>
          <p className="mt-1 text-sm text-dim">{t("login.subtitle")}</p>
          <form onSubmit={onSubmit} className="mt-6 space-y-4">
            <div>
              <label htmlFor="email" className="label">
                {t("login.email")}
              </label>
              <input
                id="email"
                type="email"
                autoComplete="username"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="input"
              />
            </div>
            <div>
              <label htmlFor="password" className="label">
                {t("login.password")}
              </label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input"
              />
            </div>
            {error && <ErrorState error={error} compact />}
            <Button type="submit" variant="primary" loading={submitting} className="w-full">
              {submitting ? t("login.submitting") : t("login.submit")}
            </Button>
          </form>
        </main>
        <p className="mt-6 text-center text-xs text-dim">{t("app.by")}</p>
      </div>
    </div>
  );
}
