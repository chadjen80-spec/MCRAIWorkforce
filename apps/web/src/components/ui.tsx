"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";
import { CLIENT_ERROR_CODES, type ApiError } from "@/lib/api";
import { useI18n } from "@/i18n/I18nProvider";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cx("rounded-2xl border border-line bg-surface p-4 sm:p-5", className)}>{children}</section>;
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-dim">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-3">
      <h2 className="text-base font-semibold text-ink">{children}</h2>
      {aside}
    </div>
  );
}

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-gradient-to-r from-brand to-cyan text-[#04121f] font-semibold hover:brightness-110 shadow-[0_0_24px_-8px_var(--color-cyan)]",
  secondary: "border border-line bg-raised text-ink hover:border-brand-soft/60",
  danger: "border border-bad/50 bg-bad/10 text-bad hover:bg-bad/20",
  ghost: "text-dim hover:bg-raised hover:text-ink",
};

export const buttonClass = (variant: ButtonVariant = "secondary", size: "sm" | "md" = "md") =>
  cx(
    "inline-flex items-center justify-center gap-2 rounded-lg transition disabled:cursor-not-allowed disabled:opacity-50",
    size === "sm" ? "min-h-8 px-2.5 py-1 text-xs" : "min-h-10 px-4 py-2 text-sm",
    BUTTON_VARIANTS[variant],
  );

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "sm" | "md"; loading?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(buttonClass(variant, size), className)}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cx("inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent", className)}
    />
  );
}

export type Tone = "neutral" | "info" | "success" | "warn" | "danger";

const TONES: Record<Tone, string> = {
  neutral: "border-line bg-raised text-dim",
  info: "border-brand/40 bg-brand/15 text-brand-soft",
  success: "border-ok/40 bg-ok/10 text-ok",
  warn: "border-warn/40 bg-warn/10 text-warn",
  danger: "border-bad/40 bg-bad/10 text-bad",
};

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium",
        TONES[tone],
      )}
    >
      {children}
    </span>
  );
}

/** Must stay clearly visible on anything produced by the demo fallback. */
export function DemoBadge() {
  const { t } = useI18n();
  return (
    <span
      title={t("demo.tooltip")}
      className="inline-flex items-center rounded-md bg-warn px-1.5 py-0.5 text-[11px] font-bold tracking-wider text-[#231800]"
    >
      {t("demo.label")}
      <span className="sr-only"> — {t("demo.tooltip")}</span>
    </span>
  );
}

export function ProgressBar({ value, label, tone = "brand" }: { value: number; label: string; tone?: "brand" | "bad" }) {
  const pct = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      className="h-2 w-full overflow-hidden rounded-full bg-raised"
    >
      <div
        className={cx(
          "h-full rounded-full transition-[width] duration-500",
          tone === "bad" ? "bg-bad" : "bg-gradient-to-r from-brand to-cyan",
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function LoadingBlock({ className }: { className?: string }) {
  const { t } = useI18n();
  return (
    <div role="status" className={cx("flex items-center justify-center gap-3 py-10 text-sm text-dim", className)}>
      <Spinner />
      {t("common.loading")}
    </div>
  );
}

export function useErrorMessage(): (error: ApiError) => string {
  const { t } = useI18n();
  return (error) => {
    if (error.code === CLIENT_ERROR_CODES.network) return t("error.network");
    if (error.code === CLIENT_ERROR_CODES.badResponse) return t("error.badResponse");
    if (error.code === "UPSTREAM_UNAVAILABLE") return t("error.upstream");
    return error.message;
  };
}

/** Full error panel for a failed load. Shows the API's message and code; never placeholder data. */
export function ErrorState({ error, onRetry, compact = false }: { error: ApiError; onRetry?: () => void; compact?: boolean }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  return (
    <div
      role="alert"
      className={cx("rounded-xl border border-bad/40 bg-bad/10 text-sm", compact ? "px-3 py-2" : "px-4 py-4")}
    >
      {!compact && <p className="font-semibold text-bad">{t("error.title")}</p>}
      <p className={cx("text-ink", !compact && "mt-1")}>{message(error)}</p>
      <p className="mt-1 text-xs text-dim">
        {t("error.code")}: <code className="font-mono">{error.code}</code>
      </p>
      {onRetry && (
        <Button size="sm" className="mt-3" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      )}
    </div>
  );
}

/** Shown above real, previously loaded data when a background refresh fails. */
export function RefreshError({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const { t } = useI18n();
  const message = useErrorMessage();
  return (
    <div role="alert" className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-warn/40 bg-warn/10 px-3 py-2 text-sm">
      <span className="text-ink">
        {t("error.refreshFailed")} <span className="text-dim">({message(error)} · {error.code})</span>
      </span>
      <Button size="sm" onClick={onRetry}>
        {t("common.retry")}
      </Button>
    </div>
  );
}

export function EmptyState({ title, hint, action }: { title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line px-4 py-10 text-center">
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="mt-1 text-sm text-dim">{hint}</p>}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
    </div>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  hint,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
}) {
  return (
    <label className={cx("flex items-start justify-between gap-4", disabled ? "opacity-60" : "cursor-pointer")}>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-ink">{label}</span>
        {hint && <span className="mt-0.5 block text-xs text-dim">{hint}</span>}
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          "relative mt-0.5 h-6 w-11 shrink-0 rounded-full border transition disabled:cursor-not-allowed",
          checked ? "border-transparent bg-gradient-to-r from-brand to-cyan" : "border-line bg-raised",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 size-[18px] rounded-full bg-white transition-all",
            checked ? "left-[22px]" : "left-0.5",
          )}
        />
      </button>
    </label>
  );
}

export function Pagination({
  total,
  limit,
  offset,
  onChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  const { t, number } = useI18n();
  if (total === 0) return null;
  const from = offset + 1;
  const to = Math.min(offset + limit, total);
  return (
    <nav aria-label={t("common.pagination")} className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-dim">
        {t("common.showing", { from: number(from), to: number(to), total: number(total) })}
      </p>
      <div className="flex gap-2">
        <Button size="sm" disabled={offset <= 0} onClick={() => onChange(Math.max(0, offset - limit))}>
          {t("common.previous")}
        </Button>
        <Button size="sm" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>
          {t("common.next")}
        </Button>
      </div>
    </nav>
  );
}
