"use client";

import { useState } from "react";
import { CafeShell } from "@/components/cafe/CafeShell";
import { Badge, Button, Card, EmptyState, ErrorState, LoadingBlock, Switch } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { toApiError, type ApiError } from "@/lib/api";
import { cafeApi } from "@/lib/cafe";
import { useApi } from "@/lib/useApi";

const CATEGORIES = ["game", "launcher", "browser", "office", "tool"];
const input = "mt-1 block w-full rounded-lg border border-line bg-raised px-3 py-2 text-ink";

function Games() {
  const { t } = useI18n();
  const apps = useApi("cafe-apps-page", (s) => cafeApi.apps(s));
  const [draft, setDraft] = useState({ app_key: "", name: "", aliases: "", category: "game", requires_session: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await cafeApi.createApp({
        ...draft,
        app_key: draft.app_key.trim(),
        name: draft.name.trim(),
        aliases: draft.aliases.split(",").map((a) => a.trim()).filter(Boolean),
      });
      setDraft({ app_key: "", name: "", aliases: "", category: "game", requires_session: true });
      apps.reload();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <p className="text-sm text-dim">{t("cafe.games.note")}</p>
      <Card className="p-4">
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            void add();
          }}
        >
          <label className="text-sm text-dim">
            {t("cafe.games.key")}
            <input className={input} value={draft.app_key} pattern="[a-z0-9][a-z0-9_\-]*" maxLength={64} required
                   onChange={(e) => setDraft({ ...draft, app_key: e.target.value })} />
          </label>
          <label className="text-sm text-dim">
            {t("cafe.games.name")}
            <input className={input} value={draft.name} maxLength={128} required onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
          </label>
          <label className="text-sm text-dim">
            {t("cafe.games.aliases")}
            <input className={input} value={draft.aliases} onChange={(e) => setDraft({ ...draft, aliases: e.target.value })} />
          </label>
          <label className="text-sm text-dim">
            {t("cafe.games.category")}
            <select className={input} value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}>
              {CATEGORIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </label>
          <Switch checked={draft.requires_session} onChange={(v) => setDraft({ ...draft, requires_session: v })} label={t("cafe.games.requiresSession")} />
          <div className="flex items-end justify-end">
            <Button type="submit" variant="primary" loading={busy}>
              {t("cafe.games.add")}
            </Button>
          </div>
        </form>
        {error && (
          <div className="mt-3">
            <ErrorState error={error} compact />
          </div>
        )}
      </Card>
      {apps.loading && <LoadingBlock />}
      {apps.error && <ErrorState error={apps.error} onRetry={apps.reload} />}
      {apps.data && apps.data.items.length === 0 && <EmptyState title={t("cafe.games.empty")} />}
      {apps.data && apps.data.items.length > 0 && (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {apps.data.items.map((a) => (
            <li key={a.id}>
              <Card className="p-4">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-ink">{a.name}</span>
                  <Badge>{a.category}</Badge>
                </div>
                <p className="mt-1 font-mono text-xs text-dim">{a.app_key}</p>
                {a.aliases.length > 0 && <p className="mt-1 text-xs text-dim">{a.aliases.join(", ")}</p>}
                <div className="mt-3">
                  <Switch
                    checked={a.enabled}
                    label={t("cafe.games.enabled")}
                    onChange={async (v) => {
                      await cafeApi.patchApp(a.id, { enabled: v });
                      apps.reload();
                    }}
                  />
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CafeGamesPage() {
  return <CafeShell module="launcher">{() => <Games />}</CafeShell>;
}
