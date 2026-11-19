"use client";

import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingBlock,
  PageHeader,
  ProgressBar,
  RefreshError,
  SectionTitle,
  Switch,
} from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError, api, toApiError } from "@/lib/api";
import {
  CONFIGURABLE_PROVIDERS,
  FIREFLY_MODELS,
  type OrganizationPatch,
  type OrganizationSettings,
  type Provider,
  type ProviderUpdate,
  type Session,
  type Settings,
  type Usage,
} from "@/lib/types";
import { useApi } from "@/lib/useApi";

function SavedNote({ show }: { show: boolean }) {
  const { t } = useI18n();
  return (
    <span role="status" className="text-sm text-ok">
      {show ? t("common.saved") : ""}
    </span>
  );
}

/** Adobe Firefly: Client ID + Client Secret (write-only), model, style and whether it runs before ComfyUI. */
function FireflyForm({
  provider,
  canEdit,
  onSaved,
}: {
  provider: Provider;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  const opts = provider.options ?? {};
  const id = (name: string) => `provider-firefly-${name}`;
  const [enabled, setEnabled] = useState(provider.enabled);
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [model, setModel] = useState(opts.model ?? "");
  const [contentClass, setContentClass] = useState<"art" | "photo">(
    opts.content_class === "photo" ? "photo" : "art",
  );
  const [priority, setPriority] = useState<"first" | "fallback">(
    opts.priority === "first" ? "first" : "fallback",
  );
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const update: ProviderUpdate = {
    ...(enabled !== provider.enabled ? { enabled } : {}),
    ...(clientId.trim() !== "" ? { client_id: clientId.trim() } : {}),
    ...(secret !== "" ? { api_key: secret } : {}),
    ...(model !== (opts.model ?? "") ? { model } : {}),
    ...(contentClass !== (opts.content_class ?? "art")
      ? { content_class: contentClass }
      : {}),
    ...(priority !== (opts.priority ?? "fallback") ? { priority } : {}),
  };
  const dirty = Object.keys(update).length > 0;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dirty) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await api<Provider>("/v1/settings/providers/firefly", {
        method: "PUT",
        body: update,
      });
      setClientId("");
      setSecret("");
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(toApiError(err));
    }
    setSaving(false);
  };

  return (
    <form onSubmit={onSubmit} className="mt-3 space-y-3" autoComplete="off">
      <p className="hint">{t("firefly.intro")}</p>
      <Switch
        checked={enabled}
        onChange={setEnabled}
        label={t("settings.providerEnabled")}
        disabled={!canEdit}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={id("client")} className="label">
            {t("firefly.clientId")}
          </label>
          <input
            id={id("client")}
            type="text"
            spellCheck={false}
            value={clientId}
            onChange={(e) => setClientId(e.target.value)}
            disabled={!canEdit}
            placeholder={opts.client_id ?? ""}
            className="input font-mono"
          />
        </div>
        <div>
          <label htmlFor={id("secret")} className="label">
            {t("firefly.clientSecret")}
          </label>
          <input
            id={id("secret")}
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            disabled={!canEdit}
            className="input font-mono"
          />
        </div>
      </div>
      <p className="hint">{t("firefly.credentialsHint")}</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label htmlFor={id("priority")} className="label">
            {t("firefly.priority")}
          </label>
          <select
            id={id("priority")}
            value={priority}
            onChange={(e) =>
              setPriority(e.target.value as "first" | "fallback")
            }
            disabled={!canEdit}
            className="input"
          >
            <option value="fallback">{t("firefly.priority.fallback")}</option>
            <option value="first">{t("firefly.priority.first")}</option>
          </select>
        </div>
        <div>
          <label htmlFor={id("model")} className="label">
            {t("settings.model")}
          </label>
          <select
            id={id("model")}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={!canEdit}
            className="input"
          >
            {FIREFLY_MODELS.map((m) => (
              <option key={m} value={m}>
                {m === "" ? t("firefly.modelDefault") : m}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={id("class")} className="label">
            {t("firefly.contentClass")}
          </label>
          <select
            id={id("class")}
            value={contentClass}
            onChange={(e) => setContentClass(e.target.value as "art" | "photo")}
            disabled={!canEdit}
            className="input"
          >
            <option value="art">{t("firefly.class.art")}</option>
            <option value="photo">{t("firefly.class.photo")}</option>
          </select>
        </div>
      </div>
      <p className="hint">
        {priority === "first"
          ? t("firefly.priorityFirstHint")
          : t("firefly.priorityFallbackHint")}
      </p>
      {error && <ErrorState error={error} compact />}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          variant="primary"
          loading={saving}
          disabled={!canEdit || !dirty}
        >
          {saving ? t("common.saving") : t("common.save")}
        </Button>
        {!dirty && !saved && (
          <span className="text-xs text-dim">{t("settings.noChanges")}</span>
        )}
        <SavedNote show={saved && !dirty} />
      </div>
    </form>
  );
}

function ProviderCard({
  provider,
  canEdit,
  onSaved,
}: {
  provider: Provider;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const { t, tx } = useI18n();
  const configurable = CONFIGURABLE_PROVIDERS.includes(provider.name);
  const fieldId = (name: string) => `provider-${provider.name}-${name}`;

  const [enabled, setEnabled] = useState(provider.enabled);
  // Write-only: starts empty, is never filled from the server, and is cleared after saving.
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const isOpenAI = provider.name === "openai";
  const savedImagePriority =
    provider.options?.image_priority === "first" ? "first" : "fallback";
  const [imagePriority, setImagePriority] = useState<"first" | "fallback">(
    savedImagePriority,
  );
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const update: ProviderUpdate = {
    ...(enabled !== provider.enabled ? { enabled } : {}),
    ...(apiKey !== "" ? { api_key: apiKey } : {}),
    ...(baseUrl.trim() !== "" ? { base_url: baseUrl.trim() } : {}),
    ...(model.trim() !== "" ? { model: model.trim() } : {}),
    ...(isOpenAI && imagePriority !== savedImagePriority
      ? { image_priority: imagePriority }
      : {}),
  };
  const dirty = Object.keys(update).length > 0;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dirty) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const result = await api<Provider>(
        `/v1/settings/providers/${encodeURIComponent(provider.name)}`,
        {
          method: "PUT",
          body: update,
        },
      );
      setApiKey("");
      setBaseUrl("");
      setModel("");
      setEnabled(result.enabled);
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(toApiError(err));
    }
    setSaving(false);
  };

  return (
    <li className="rounded-xl border border-line bg-bg p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="font-mono text-sm font-semibold text-ink">
            {provider.name}
          </p>
          <p className="text-xs text-dim">
            {tx("settings.providerKind", provider.kind)} ·{" "}
            {tx("monitor.loc", provider.location)}
          </p>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge tone={provider.configured ? "success" : "neutral"}>
            {provider.configured
              ? t("settings.configured")
              : t("settings.notConfigured")}
          </Badge>
          <Badge tone={provider.enabled ? "info" : "neutral"}>
            {provider.enabled ? t("settings.enabled") : t("settings.disabled")}
          </Badge>
          {provider.configured && provider.healthy !== null && (
            <Badge tone={provider.healthy ? "success" : "danger"}>
              {provider.healthy
                ? t("settings.healthy")
                : t("settings.unhealthy")}
            </Badge>
          )}
        </div>
      </div>
      {provider.detail && (
        <p className="mt-2 text-sm text-dim">{provider.detail}</p>
      )}

      {configurable && provider.name === "firefly" && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-brand-soft">
            {t("settings.configure")}
          </summary>
          <FireflyForm
            provider={provider}
            canEdit={canEdit}
            onSaved={onSaved}
          />
        </details>
      )}
      {configurable && provider.name !== "firefly" && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-brand-soft">
            {t("settings.configure")}
          </summary>
          <form
            onSubmit={onSubmit}
            className="mt-3 space-y-3"
            autoComplete="off"
          >
            <Switch
              checked={enabled}
              onChange={setEnabled}
              label={t("settings.providerEnabled")}
              disabled={!canEdit}
            />
            <div>
              <label htmlFor={fieldId("key")} className="label">
                {t("settings.apiKey")}
              </label>
              <input
                id={fieldId("key")}
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                disabled={!canEdit}
                aria-describedby={fieldId("key-hint")}
                className="input font-mono"
              />
              <p id={fieldId("key-hint")} className="hint">
                {t("settings.apiKeyHint")}
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor={fieldId("url")} className="label">
                  {t("settings.baseUrl")}
                </label>
                <input
                  id={fieldId("url")}
                  type="url"
                  inputMode="url"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  disabled={!canEdit}
                  className="input"
                />
              </div>
              <div>
                <label htmlFor={fieldId("model")} className="label">
                  {t("settings.model")}
                </label>
                <input
                  id={fieldId("model")}
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  disabled={!canEdit}
                  className="input"
                />
              </div>
            </div>
            {isOpenAI && (
              <div>
                <label htmlFor={fieldId("image-priority")} className="label">
                  {t("settings.openaiImagePriority")}
                </label>
                <select
                  id={fieldId("image-priority")}
                  value={imagePriority}
                  onChange={(e) =>
                    setImagePriority(e.target.value as "first" | "fallback")
                  }
                  disabled={!canEdit}
                  className="input"
                >
                  <option value="fallback">
                    {t("firefly.priority.fallback")}
                  </option>
                  <option value="first">{t("firefly.priority.first")}</option>
                </select>
                <p className="hint">
                  {imagePriority === "first"
                    ? t("settings.openaiImageFirstHint")
                    : t("settings.openaiImageFallbackHint")}
                </p>
              </div>
            )}
            <p className="hint">{t("settings.blankKeeps")}</p>
            {error && <ErrorState error={error} compact />}
            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="submit"
                variant="primary"
                loading={saving}
                disabled={!canEdit || !dirty}
              >
                {saving ? t("common.saving") : t("common.save")}
              </Button>
              {!dirty && !saved && (
                <span className="text-xs text-dim">
                  {t("settings.noChanges")}
                </span>
              )}
              <SavedNote show={saved && !dirty} />
            </div>
          </form>
        </details>
      )}
    </li>
  );
}

function OrganizationForm({
  organization,
  canEdit,
  onSaved,
}: {
  organization: OrganizationSettings;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const { t } = useI18n();
  // `base` is the last server-confirmed state; the form only sends fields that differ from it.
  const [base, setBase] = useState(organization);
  const [name, setName] = useState(organization.name);
  const [monthly, setMonthly] = useState(
    String(organization.monthly_budget_usd),
  );
  const [perJob, setPerJob] = useState(
    String(organization.default_job_budget_usd),
  );
  const [cloudFallback, setCloudFallback] = useState(
    organization.allow_cloud_fallback,
  );
  const [externalRestricted, setExternalRestricted] = useState(
    organization.allow_external_ai_for_restricted,
  );
  const [context, setContext] = useState(organization.business_context ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [invalid, setInvalid] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const parse = (v: string) => (v.trim() === "" ? NaN : Number(v));
  const monthlyValue = parse(monthly);
  const perJobValue = parse(perJob);

  const patch: OrganizationPatch = {
    ...(name.trim() !== base.name ? { name: name.trim() } : {}),
    ...(monthlyValue !== base.monthly_budget_usd
      ? { monthly_budget_usd: monthlyValue }
      : {}),
    ...(perJobValue !== base.default_job_budget_usd
      ? { default_job_budget_usd: perJobValue }
      : {}),
    ...(cloudFallback !== base.allow_cloud_fallback
      ? { allow_cloud_fallback: cloudFallback }
      : {}),
    ...(externalRestricted !== base.allow_external_ai_for_restricted
      ? { allow_external_ai_for_restricted: externalRestricted }
      : {}),
    ...(context !== (base.business_context ?? "")
      ? { business_context: context }
      : {}),
  };
  const dirty = Object.keys(patch).length > 0;

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dirty) return;
    const numbersOk = [monthlyValue, perJobValue].every(
      (n) => Number.isFinite(n) && n >= 0,
    );
    setInvalid(!numbersOk);
    if (!numbersOk) return;

    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const result = await api<OrganizationSettings>(
        "/v1/settings/organization",
        { method: "PATCH", body: patch },
      );
      setBase(result);
      setName(result.name);
      setMonthly(String(result.monthly_budget_usd));
      setPerJob(String(result.default_job_budget_usd));
      setCloudFallback(result.allow_cloud_fallback);
      setExternalRestricted(result.allow_external_ai_for_restricted);
      setContext(result.business_context ?? "");
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(toApiError(err));
    }
    setSaving(false);
  };

  return (
    <form onSubmit={onSubmit} className="space-y-4" noValidate>
      <div>
        <label htmlFor="org-name" className="label">
          {t("settings.orgName")}
        </label>
        <input
          id="org-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={!canEdit}
          className="input"
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="org-monthly" className="label">
            {t("settings.monthlyBudget")}
          </label>
          <input
            id="org-monthly"
            type="number"
            inputMode="decimal"
            min="0"
            step="0.01"
            value={monthly}
            onChange={(e) => setMonthly(e.target.value)}
            disabled={!canEdit}
            className="input"
          />
        </div>
        <div>
          <label htmlFor="org-per-job" className="label">
            {t("settings.defaultJobBudget")}
          </label>
          <input
            id="org-per-job"
            type="number"
            inputMode="decimal"
            min="0"
            step="0.01"
            value={perJob}
            onChange={(e) => setPerJob(e.target.value)}
            disabled={!canEdit}
            className="input"
          />
        </div>
      </div>
      {invalid && (
        <p role="alert" className="text-sm text-bad">
          {t("settings.invalidNumber")}
        </p>
      )}
      <Switch
        checked={cloudFallback}
        onChange={setCloudFallback}
        label={t("settings.allowCloudFallback")}
        hint={t("settings.allowCloudFallbackHint")}
        disabled={!canEdit}
      />
      <Switch
        checked={externalRestricted}
        onChange={setExternalRestricted}
        label={t("settings.allowExternalRestricted")}
        hint={t("settings.allowExternalRestrictedHint")}
        disabled={!canEdit}
      />
      <div>
        <label htmlFor="org-context" className="label">
          {t("settings.businessContext")}
        </label>
        <textarea
          id="org-context"
          rows={5}
          value={context}
          onChange={(e) => setContext(e.target.value)}
          disabled={!canEdit}
          aria-describedby="org-context-hint"
          className="input"
        />
        <p id="org-context-hint" className="hint">
          {t("settings.businessContextHint")}
        </p>
      </div>
      {error && <ErrorState error={error} compact />}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="submit"
          variant="primary"
          loading={saving}
          disabled={!canEdit || !dirty}
        >
          {saving ? t("common.saving") : t("common.save")}
        </Button>
        <SavedNote show={saved && !dirty} />
      </div>
    </form>
  );
}

function Breakdown({
  title,
  rows,
}: {
  title: string;
  rows: { label: string; cost: number; events: number }[];
}) {
  const { t, money, number } = useI18n();
  const max = Math.max(...rows.map((r) => r.cost), 0);
  return (
    <div>
      <h3 className="mb-2 text-sm font-medium text-ink">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-sm text-dim">{t("settings.noUsage")}</p>
      ) : (
        <ul className="space-y-2.5">
          {rows.map((row) => (
            <li key={row.label}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-sm">
                <span className="text-ink">{row.label}</span>
                <span className="tabular-nums text-dim">
                  <span className="text-ink">{money(row.cost)}</span> ·{" "}
                  {t("settings.events", { n: number(row.events) })}
                </span>
              </div>
              <div
                aria-hidden="true"
                className="mt-1 h-1.5 overflow-hidden rounded-full bg-raised"
              >
                <div
                  className="h-full rounded-full bg-brand"
                  style={{ width: `${max > 0 ? (row.cost / max) * 100 : 0}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function DailyBars({ daily }: { daily: Usage["daily"] }) {
  const { t, money, date } = useI18n();
  const max = Math.max(...daily.map((d) => d.cost_usd), 0);
  if (daily.length === 0)
    return <p className="text-sm text-dim">{t("settings.noUsage")}</p>;
  return (
    <div>
      <div className="mb-1 text-right text-xs tabular-nums text-dim">
        {money(max)}
      </div>
      <ul
        className="flex h-36 items-end gap-[3px] border-b border-line"
        aria-label={t("settings.daily")}
      >
        {daily.map((d) => {
          const label = `${date(d.date)}: ${money(d.cost_usd)}`;
          return (
            <li
              key={d.date}
              title={label}
              className="group flex h-full min-w-0 flex-1 items-end"
            >
              <span className="sr-only">{label}</span>
              <span
                aria-hidden="true"
                className="w-full rounded-t-sm bg-brand transition group-hover:bg-cyan"
                style={{
                  height: `${max > 0 ? Math.max((d.cost_usd / max) * 100, d.cost_usd > 0 ? 3 : 0) : 0}%`,
                }}
              />
            </li>
          );
        })}
      </ul>
      <div className="mt-1 flex justify-between text-xs text-dim">
        <span>{date(daily[0].date)}</span>
        {daily.length > 1 && <span>{date(daily[daily.length - 1].date)}</span>}
      </div>
    </div>
  );
}

function UsageSection() {
  const { t, tx, money, date } = useI18n();
  const usage = useApi<Usage>("usage", (signal) =>
    api<Usage>("/v1/usage", { signal }),
  );
  const u = usage.data;
  const hasBudget = !!u && u.monthly_budget_usd > 0;
  const pct =
    u && hasBudget ? (u.total_cost_usd / u.monthly_budget_usd) * 100 : 0;

  return (
    <Card>
      <SectionTitle
        aside={
          u && (
            <span className="text-xs text-dim">
              {t("settings.usagePeriod", {
                from: date(u.period.from),
                to: date(u.period.to),
              })}
            </span>
          )
        }
      >
        {t("settings.usage")}
      </SectionTitle>
      {usage.loading && <LoadingBlock />}
      {usage.error && !u && (
        <ErrorState error={usage.error} onRetry={usage.reload} />
      )}
      {usage.error && u && (
        <RefreshError error={usage.error} onRetry={usage.reload} />
      )}
      {u && (
        <div className="space-y-6">
          <div>
            <p className="text-sm text-dim">{t("settings.usageTotal")}</p>
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-3xl font-semibold tabular-nums text-ink">
                {money(u.total_cost_usd)}
              </span>
              <span className="text-sm text-dim">
                {hasBudget
                  ? t("dashboard.costOf", {
                      budget: money(u.monthly_budget_usd),
                    })
                  : t("dashboard.noBudget")}
              </span>
            </div>
            {hasBudget && (
              <div className="mt-3">
                <ProgressBar
                  value={pct}
                  label={t("settings.usageTotal")}
                  tone={pct > 100 ? "bad" : "brand"}
                />
                {pct > 100 && (
                  <p className="mt-2 text-sm text-bad">
                    {t("dashboard.overBudget")}
                  </p>
                )}
              </div>
            )}
          </div>
          <div className="grid gap-6 md:grid-cols-2">
            <Breakdown
              title={t("settings.byProvider")}
              rows={u.by_provider.map((r) => ({
                label: r.provider,
                cost: r.cost_usd,
                events: r.events,
              }))}
            />
            <Breakdown
              title={t("settings.byAgent")}
              rows={u.by_agent.map((r) => ({
                label: tx("agent", r.agent),
                cost: r.cost_usd,
                events: r.events,
              }))}
            />
          </div>
          <div>
            <h3 className="mb-2 text-sm font-medium text-ink">
              {t("settings.daily")}
            </h3>
            <DailyBars daily={u.daily} />
          </div>
        </div>
      )}
    </Card>
  );
}

function PasswordForm({ email }: { email: string }) {
  const { t } = useI18n();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setDone(false);
    if (next.length < 10) return setProblem(t("settings.passwordErr.short"));
    if (next !== confirm) return setProblem(t("settings.passwordErr.mismatch"));
    if (next === current) return setProblem(t("settings.passwordErr.same"));
    setProblem(null);
    setSaving(true);
    try {
      await api<void>("/v1/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      const apiError = toApiError(err);
      setProblem(
        apiError.code === "WRONG_PASSWORD"
          ? t("settings.passwordErr.wrong")
          : apiError.message,
      );
    }
    setSaving(false);
  };

  return (
    <form onSubmit={onSubmit} className="max-w-md space-y-4" noValidate>
      <p className="text-sm text-dim">
        {t("settings.passwordHint", { email })}
      </p>
      {/* lets password managers attach the new password to the right account */}
      <input
        type="text"
        name="username"
        autoComplete="username"
        value={email}
        readOnly
        hidden
      />
      <div>
        <label htmlFor="pw-current" className="label">
          {t("settings.passwordCurrent")}
        </label>
        <input
          id="pw-current"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          className="input"
          required
        />
      </div>
      <div>
        <label htmlFor="pw-new" className="label">
          {t("settings.passwordNew")}
        </label>
        <input
          id="pw-new"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          aria-describedby="pw-rule"
          className="input"
          minLength={10}
          maxLength={128}
          required
        />
        <p id="pw-rule" className="hint">
          {t("settings.passwordRule")}
        </p>
      </div>
      <div>
        <label htmlFor="pw-confirm" className="label">
          {t("settings.passwordConfirm")}
        </label>
        <input
          id="pw-confirm"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          className="input"
          maxLength={128}
          required
        />
      </div>
      {problem && (
        <p role="alert" className="text-sm text-bad">
          {problem}
        </p>
      )}
      {done && (
        <p role="status" className="text-sm text-ok">
          {t("settings.passwordChanged")}
        </p>
      )}
      <Button
        type="submit"
        variant="primary"
        loading={saving}
        disabled={!current || !next || !confirm}
      >
        {t("settings.passwordSave")}
      </Button>
    </form>
  );
}

export default function SettingsPage() {
  const { t, number } = useI18n();
  const settings = useApi<Settings>("settings", (signal) =>
    api<Settings>("/v1/settings", { signal }),
  );
  const me = useApi<Session>("settings-me", (signal) =>
    api<Session>("/v1/auth/me", { signal }),
  );
  // The API enforces owner/admin; this only decides whether to lock the forms up front.
  const canEdit = me.data ? me.data.role !== "member" : true;
  const s = settings.data;

  return (
    <>
      <PageHeader
        title={t("settings.title")}
        subtitle={t("settings.subtitle")}
      />

      {me.data && !canEdit && (
        <p className="mb-4 rounded-xl border border-line bg-raised px-3 py-2 text-sm text-dim">
          {t("settings.memberReadOnly")}
        </p>
      )}

      {settings.loading && <LoadingBlock />}
      {settings.error && !s && (
        <ErrorState error={settings.error} onRetry={settings.reload} />
      )}
      {settings.error && s && (
        <RefreshError error={settings.error} onRetry={settings.reload} />
      )}

      <div className="space-y-4">
        {s && (
          <>
            <Card>
              <SectionTitle>{t("settings.providers")}</SectionTitle>
              {s.providers.length === 0 ? (
                <EmptyState title={t("settings.providersEmpty")} />
              ) : (
                <ul className="grid gap-3 lg:grid-cols-2">
                  {s.providers.map((p) => (
                    <ProviderCard
                      key={p.name}
                      provider={p}
                      canEdit={canEdit}
                      onSaved={settings.reload}
                    />
                  ))}
                </ul>
              )}
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <SectionTitle>{t("settings.workers")}</SectionTitle>
                {s.workers.length === 0 ? (
                  <EmptyState title={t("settings.workersEmpty")} />
                ) : (
                  <ul className="divide-y divide-line">
                    {s.workers.map((w) => (
                      <li
                        key={w.queue}
                        className="flex flex-wrap items-center justify-between gap-2 py-2.5"
                      >
                        <span className="font-mono text-sm text-ink">
                          {w.queue}
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="text-xs tabular-nums text-dim">
                            {t("settings.workerCount", {
                              n: number(w.workers),
                            })}
                          </span>
                          <Badge tone={w.online ? "success" : "danger"}>
                            {w.online
                              ? t("settings.online")
                              : t("settings.offline")}
                          </Badge>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>

              <Card>
                <SectionTitle
                  aside={
                    <Badge tone={s.demo_mode_enabled ? "warn" : "neutral"}>
                      {s.demo_mode_enabled
                        ? t("settings.enabled")
                        : t("settings.disabled")}
                    </Badge>
                  }
                >
                  {t("settings.demoMode")}
                </SectionTitle>
                <p className="text-sm text-dim">
                  {s.demo_mode_enabled
                    ? t("settings.demoOn")
                    : t("settings.demoOff")}
                </p>
              </Card>
            </div>

            <Card>
              <SectionTitle>{t("settings.org")}</SectionTitle>
              <OrganizationForm
                organization={s.organization}
                canEdit={canEdit}
                onSaved={settings.reload}
              />
            </Card>
          </>
        )}

        {me.data?.user && (
          <Card>
            <SectionTitle>{t("settings.password")}</SectionTitle>
            <PasswordForm email={me.data.user.email} />
          </Card>
        )}

        <UsageSection />
      </div>
    </>
  );
}
