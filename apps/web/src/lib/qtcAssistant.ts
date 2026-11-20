// Mira's sales and finance commands. The server classifies the text with fixed rules (no AI, no cost) and
// answers from real records. Anything that writes (a draft quotation, reminder drafts) comes back with
// `confirm` and runs only after a person says yes. `intent: null` means "not a sales/finance command":
// Mira then carries on with her usual creative-job flow, unchanged.

import type { Locale } from "@/i18n/dictionaries";
import { api } from "@/lib/api";
import { decide } from "@/lib/intent";

export type QtcIntent = "create_quotation" | "pending_quotations" | "unpaid_customers" | "receivables_summary" | "collection_drafts";

export interface QtcConfirm {
  action: "create_quotation" | "draft_reminders";
  payload: Record<string, unknown>;
}

export interface QtcAnswer {
  intent: QtcIntent | null;
  agent: string | null;
  reply: string;
  data: {
    link?: string;
    items?: Record<string, unknown>[];
    lines?: { description: string; approved_price: string | null; unit?: string }[];
    figures?: Record<string, string | number>;
    [key: string]: unknown;
  };
  confirm: QtcConfirm | null;
}

/** Ask the server whether this is a sales/finance command. Any failure counts as "no": Mira's usual flow runs. */
export async function askQtc(text: string, locale: Locale, signal?: AbortSignal): Promise<QtcAnswer | null> {
  try {
    const answer = await api<QtcAnswer>("/v1/qtc/assistant", { method: "POST", body: { text, language: locale }, signal });
    return answer.intent ? answer : null;
  } catch {
    return null;
  }
}

export function confirmQtc(confirm: QtcConfirm, locale: Locale, idempotencyKey: string): Promise<QtcAnswer> {
  return api<QtcAnswer>("/v1/qtc/assistant/confirm", {
    method: "POST",
    body: { action: confirm.action, payload: confirm.payload, language: locale, idempotency_key: idempotencyKey },
  });
}

/** The spoken answer to a sales/finance proposal: only an explicit yes runs it. ("Design" means nothing here.) */
export function qtcDecision(answer: string, locale: Locale): "yes" | "no" | "unclear" {
  const verdict = decide(answer, locale);
  return verdict === "yes" || verdict === "no" ? verdict : "unclear";
}
