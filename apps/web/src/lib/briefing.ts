// What Mira says when the Assistant opens, built from the same snapshot the operations panel shows, with
// the budget formatted by the same function, so the spoken figures and the on-screen figures match.

import type { MessageKey } from "@/i18n/dictionaries";
import type { DashboardSummary } from "./types";

type T = (key: MessageKey, vars?: Record<string, string>) => string;

/** Month spend and budget as both the briefing and the panel display them. */
export function budgetFigures(s: Pick<DashboardSummary, "month_cost_usd" | "monthly_budget_usd">) {
  const pct = s.monthly_budget_usd > 0 ? Math.min(100, (s.month_cost_usd / s.monthly_budget_usd) * 100) : 0;
  return { spent: s.month_cost_usd.toFixed(2), budget: s.monthly_budget_usd.toFixed(0), pct };
}

/** The spoken welcome: greets the user by first name and reviews the workforce right now. */
export function workforceBriefing(t: T, firstName: string, s: DashboardSummary | null | undefined): string {
  const parts = [firstName ? t("brief.hello", { name: firstName }) : t("brief.helloNoName")];
  if (s) {
    const busy: string[] = [];
    if (s.active_jobs > 0) busy.push(t("brief.running", { n: String(s.active_jobs) }));
    if (s.awaiting_approval_jobs > 0) busy.push(t("brief.awaiting", { n: String(s.awaiting_approval_jobs) }));
    if (s.failed_jobs > 0) busy.push(t("brief.failed", { n: String(s.failed_jobs) }));
    parts.push(busy.length ? t("brief.status", { items: busy.join(" ") }) : t("brief.idle"));
    const { spent, budget } = budgetFigures(s);
    parts.push(t("brief.spend", { spent, budget }));
    if ((s.offline_queues?.length ?? 0) > 0) parts.push(t("brief.workersDown"));
  }
  return parts.join(" ");
}
