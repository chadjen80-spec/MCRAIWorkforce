"use client";

// Voice control on every Sales & Finance page. Say a page name ("ใบแจ้งหนี้", "เพิ่มลูกค้า", "Payments") to open it,
// or ask what Mira answers ("ลูกค้ารายไหนยังไม่จ่ายเงิน", "สรุปยอดลูกหนี้เดือนนี้"). Proposals that write data (a draft
// quotation, reminder drafts) run only after Confirm, exactly as on Mira's screen. Nothing is sent to customers.

import { useRouter } from "next/navigation";
import { useRef, useState, useSyncExternalStore } from "react";
import { QtcResultCard } from "@/components/assistant/QtcResultCard";
import { Button, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { newIdempotencyKey, toApiError } from "@/lib/api";
import { qtcPageFor } from "@/lib/intent";
import { type QtcAnswer, askQtc, confirmQtc } from "@/lib/qtcAssistant";
import { VoiceError, canListen, listenOnce, speak } from "@/lib/voice";

const noop = () => () => {};

export function QtcVoice() {
  const { t, locale } = useI18n();
  const router = useRouter();
  const tag = locale === "th" ? "th-TH" : "en-US";
  // The browser's speech recognition only exists on the client.
  const supported = useSyncExternalStore(noop, canListen, () => false);
  const [state, setState] = useState<"idle" | "listening" | "thinking" | "confirming">("idle");
  const [heard, setHeard] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [answer, setAnswer] = useState<{ result: QtcAnswer; waiting: boolean; key: string } | null>(null);
  const session = useRef<AbortController | null>(null);

  const say = (text: string) => void speak(text, tag).catch(() => undefined);

  const listen = async () => {
    if (state === "listening" || state === "thinking") {
      session.current?.abort();
      setState("idle");
      return;
    }
    const controller = new AbortController();
    session.current = controller;
    setMessage(null);
    setAnswer(null);
    setHeard("");
    setState("listening");
    try {
      const text = await listenOnce(tag, { onInterim: setHeard, signal: controller.signal });
      setHeard(text);
      const page = qtcPageFor(text);
      if (page) {
        say(t(`assistant.openPage.${page.name}` as MessageKey));
        setHeard("");
        router.push(page.path);
        return;
      }
      setState("thinking");
      const result = await askQtc(text, locale, controller.signal);
      if (controller.signal.aborted) return;
      if (result) {
        setAnswer({ result, waiting: Boolean(result.confirm), key: newIdempotencyKey() });
        say(result.reply);
      } else {
        setMessage(t("qtcVoice.notUnderstood"));
      }
    } catch (e) {
      if (e instanceof VoiceError) {
        if (e.code !== "aborted") setMessage(e.code === "no-speech" ? t("qtcVoice.notHeard") : e.code === "unsupported" ? t("voice.unsupported") : t(`voice.err.${e.code}` as MessageKey));
      } else {
        setMessage(t("qtcVoice.failed"));
      }
    } finally {
      if (!controller.signal.aborted) setState("idle");
    }
  };

  const confirm = async () => {
    if (!answer?.result.confirm) return;
    setState("confirming");
    try {
      const done = await confirmQtc(answer.result.confirm, locale, answer.key);
      setAnswer({ result: done, waiting: false, key: answer.key });
      say(done.reply);
    } catch (e) {
      setMessage(t("qtcMira.failed", { message: toApiError(e).message }));
    } finally {
      setState("idle");
    }
  };

  const busy = state === "listening" || state === "thinking";
  const open = busy || heard !== "" || message !== null || answer !== null;
  return (
    <div className="relative">
      <Button
        variant={busy ? "primary" : "secondary"}
        onClick={() => void listen()}
        disabled={!supported}
        aria-pressed={busy}
        title={supported ? t("qtcVoice.hint") : t("voice.unsupported")}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className={cx("size-4", state === "listening" && "animate-pulse")} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zM5 11a7 7 0 0 0 14 0M12 18v3" />
        </svg>
        {state === "listening" ? t("qtcVoice.listening") : state === "thinking" ? t("qtcVoice.thinking") : t("qtcVoice.button")}
      </Button>
      {open && (
        <div
          role="status"
          aria-live="polite"
          className="absolute right-0 z-30 mt-2 w-[min(28rem,calc(100vw-2rem))] space-y-2 rounded-2xl border border-line bg-surface p-3 shadow-xl"
        >
          {state === "listening" && !heard && <p className="text-sm text-dim">{t("qtcVoice.hint")}</p>}
          {heard && <p className="text-sm text-ink">“{heard}”</p>}
          {message && <p className="text-sm text-warn">{message}</p>}
          {answer && (
            <QtcResultCard
              answer={answer.result}
              waiting={answer.waiting}
              busy={state === "confirming"}
              onConfirm={() => void confirm()}
              onCancel={() => setAnswer(null)}
            />
          )}
          {!busy && (
            <button type="button" className="text-xs text-dim underline-offset-2 hover:underline" onClick={() => { setHeard(""); setMessage(null); setAnswer(null); }}>
              {t("qtcVoice.close")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
