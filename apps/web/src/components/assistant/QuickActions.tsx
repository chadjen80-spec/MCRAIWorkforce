"use client";

import { cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { PLANNED, WORKFLOWS, offlineFor, type Workflow } from "@/lib/workflows";

/**
 * Starting points for the workflows the team can run today. Picking one never starts a paid job: a
 * command card fills the composer for you to finish and confirm, and the poster card opens the free
 * design on Mira's screen. Quote-to-Cash steps that are not built yet are listed as "Planned" text.
 */
export function QuickActions({
  onPick,
  offline,
  disabled = false,
  disabledReason,
}: {
  onPick: (workflow: Workflow) => void;
  /** Agent queues with no live worker (from the dashboard summary). */
  offline?: readonly string[];
  disabled?: boolean;
  disabledReason?: string;
}) {
  const { t } = useI18n();
  return (
    <section aria-labelledby="quick-actions-title" className="space-y-3">
      <div>
        <h2 id="quick-actions-title" className="text-lg font-semibold text-ink">
          {t("qa.title")}
        </h2>
        <p className="text-sm leading-relaxed text-dim">{disabled && disabledReason ? disabledReason : t("qa.subtitle")}</p>
      </div>
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {WORKFLOWS.map((w) => {
          const down = offlineFor(w, offline);
          return (
            <li key={w.id}>
              <button
                type="button"
                onClick={() => onPick(w)}
                disabled={disabled}
                aria-describedby={down.length ? `qa-offline-${w.id}` : undefined}
                className="group flex h-full w-full items-start gap-3 rounded-xl border border-line bg-surface p-3.5 text-left transition hover:border-brand-soft/60 hover:bg-raised disabled:cursor-not-allowed disabled:opacity-50"
              >
                <span aria-hidden="true" className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-brand/15 text-brand-soft group-hover:bg-brand/25">
                  <WorkflowIcon icon={w.icon} />
                </span>
                <span className="min-w-0">
                  <span className="block font-semibold leading-snug text-ink">{t(w.title)}</span>
                  <span className="mt-0.5 block text-sm leading-relaxed text-dim">{t(w.description)}</span>
                  {down.length > 0 && (
                    <span id={`qa-offline-${w.id}`} className="mt-1.5 block text-xs font-medium text-warn">
                      {t("qa.offline", { queues: down.join(", ") })}
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <PlannedSteps />
    </section>
  );
}

/** Not-yet-built business steps: informational only, deliberately not interactive. */
export function PlannedSteps() {
  const { t } = useI18n();
  return (
    <div className="rounded-xl border border-dashed border-line p-3.5" aria-labelledby="planned-title">
      <p id="planned-title" className="font-semibold text-ink">
        {t("planned.title")}
      </p>
      <p className="text-sm leading-relaxed text-dim">{t("planned.hint")}</p>
      <ul className="mt-2 flex flex-wrap gap-2">
        {PLANNED.map((p) => (
          <li key={p.id} data-planned={p.id} className="inline-flex items-center gap-2 rounded-full border border-line bg-bg px-3 py-1 text-sm text-dim">
            {t(p.title)}
            <span className="rounded-full bg-raised px-2 py-px text-xs font-medium text-dim">{t("planned.badge")}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function WorkflowIcon({ icon }: { icon: Workflow["icon"] }) {
  const p = { viewBox: "0 0 24 24", className: "size-5", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;
  const paths: Record<Workflow["icon"], string> = {
    images: "M4 5h16v14H4zM4 15l4-4 4 4 3-3 5 5M15.5 9.5h.01",
    captions: "M4 5h16v11H9l-5 4zM8 9h8M8 12h5",
    poster: "M6 3h12v18H6zM9 7h6M9 10h6M9 17h6",
    video: "M4 6h11v12H4zM15 10l5-3v10l-5-3",
    quote: "M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 15h5M10 18h3",
    doc: "M7 3h7l4 4v14H7zM14 3v4h4M10 13h5M10 16h5",
  };
  return (
    <svg {...p} className={cx(p.className)}>
      <path d={paths[icon]} />
    </svg>
  );
}
