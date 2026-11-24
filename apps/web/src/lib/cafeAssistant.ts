// Mira's MCR Smart Café commands ("เปิด Steam ที่เครื่อง 12", "เหลือเวลาเท่าไหร่", "เรียกพนักงาน").
// The server turns the text into one structured tool call and answers from real records; actions (launching an app,
// calling staff) come back with `confirm` and run only after a yes. The reply after a launch is the seat PC's real result.
// When the café is switched off the server answers 404 (or intent null) and Mira carries on with her usual flows.

import type { Locale } from "@/i18n/dictionaries";
import { api } from "@/lib/api";
import type { QtcAnswer, QtcConfirm } from "@/lib/qtcAssistant";

export interface CafeToolCall {
  name: string;
  arguments: Record<string, unknown>;
  source: "rules" | "model";
}

interface CafeReply extends Omit<QtcAnswer, "agent" | "source"> {
  tool_call?: CafeToolCall | null;
}

function shape(answer: CafeReply): QtcAnswer {
  const { tool_call, ...rest } = answer;
  return { ...rest, agent: "cafe", source: "cafe", data: { ...answer.data, ...(tool_call ? { tool_call } : {}) } };
}

/** Is this a café command? Any failure (café disabled, network) counts as "no". */
export async function askCafe(text: string, locale: Locale, signal?: AbortSignal): Promise<QtcAnswer | null> {
  try {
    const answer = await api<CafeReply>("/v1/cafe/assistant", { method: "POST", body: { text, language: locale }, signal });
    return answer.intent ? shape(answer) : null;
  } catch {
    return null;
  }
}

export async function confirmCafe(confirm: QtcConfirm, locale: Locale, idempotencyKey: string): Promise<QtcAnswer> {
  const done = await api<CafeReply>("/v1/cafe/assistant/confirm", {
    method: "POST",
    body: { action: confirm.action, payload: confirm.payload, language: locale, idempotency_key: idempotencyKey },
  });
  return shape(done);
}
