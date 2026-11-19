"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Button, Card, ErrorState, PageHeader, Switch, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { ApiError, api, newIdempotencyKey, toApiError } from "@/lib/api";
import { useSpeechInput } from "@/lib/useSpeechInput";
import {
  DATA_CLASSIFICATIONS,
  REQUESTABLE_AGENTS,
  type AspectRatio,
  type CreateJobRequest,
  type DataClassification,
  type Job,
  type Language,
  type RequestableAgent,
} from "@/lib/types";

const MIN = 5;
const MAX = 4000;

// Spoken phrases that submit the job when they END an utterance. Deliberately two words or more
// so ordinary dictation is unlikely to trigger them; a cancellable countdown follows anyway.
const SUBMIT_PHRASES: Record<Language, string[]> = {
  th: ["สั่งงานเลย", "เริ่มทำงานเลย"],
  en: ["execute command", "start the job"],
};
const COUNTDOWN_SECONDS = 3;

/** Splits "…text <submit phrase>" into the text and whether the phrase was said. Tolerates spaces the recogniser inserts. */
function splitSubmitPhrase(utterance: string, language: Language): { text: string; submit: boolean } {
  const clean = utterance.trim().replace(/[.!?。]+$/u, "").trim();
  for (const phrase of SUBMIT_PHRASES[language]) {
    const pattern = [...phrase.replace(/\s+/g, "")].map((ch) => ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("\\s*");
    const match = new RegExp(`(?:^|\\s)${pattern}$`, "iu").exec(clean) ?? (language === "th" ? new RegExp(`${pattern}$`, "u").exec(clean) : null);
    if (match) return { text: clean.slice(0, match.index).trim(), submit: true };
  }
  return { text: utterance.trim(), submit: false };
}

export default function WorkforcePage() {
  const { t } = useI18n();
  const router = useRouter();

  const [command, setCommand] = useState("");
  const [agents, setAgents] = useState<RequestableAgent[]>([...REQUESTABLE_AGENTS]);
  const [budget, setBudget] = useState("");
  const [language, setLanguage] = useState<Language>("th");
  const [aspect, setAspect] = useState<AspectRatio>("16:9");
  const [classification, setClassification] = useState<DataClassification>("internal");
  const [requireApproval, setRequireApproval] = useState(true);

  const [submitting, setSubmitting] = useState(false);
  const [validation, setValidation] = useState<MessageKey | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  // One Idempotency-Key per distinct submission. A retry of the same payload
  // (e.g. after a network error) reuses the key so the API cannot create a duplicate.
  const attempt = useRef<{ key: string; payload: string } | null>(null);

  // Voice dictation appends recognised phrases to the command. Ending an utterance with a submit
  // phrase stops listening and starts a short countdown the user can cancel before the job is sent.
  const [countdown, setCountdown] = useState<number | null>(null);
  const stopVoice = useRef<() => void>(() => {});
  const onSpeech = (utterance: string) => {
    const { text, submit } = splitSubmitPhrase(utterance, language);
    if (text) setCommand((prev) => (prev.trim() ? `${prev.trimEnd()} ${text}` : text).slice(0, MAX));
    if (submit) {
      stopVoice.current();
      setCountdown(COUNTDOWN_SECONDS);
    }
  };
  const voice = useSpeechInput({ lang: language === "th" ? "th-TH" : "en-US", onFinal: onSpeech });
  useEffect(() => {
    stopVoice.current = voice.stop;
  }, [voice.stop]);

  const length = command.trim().length;
  const lengthOk = length >= MIN && length <= MAX;

  const toggleAgent = (agent: RequestableAgent) =>
    setAgents((prev) => (prev.includes(agent) ? prev.filter((a) => a !== agent) : [...prev, agent]));

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setCountdown(null);
    void submit();
  };

  const submit = async () => {
    setError(null);

    if (!lengthOk) return setValidation("workforce.err.commandLength");
    if (agents.length === 0) return setValidation("workforce.err.noAgent");
    let budgetValue: number | undefined;
    if (budget.trim() !== "") {
      budgetValue = Number(budget);
      if (!Number.isFinite(budgetValue) || budgetValue <= 0) return setValidation("workforce.err.budget");
    }
    setValidation(null);

    const body: CreateJobRequest = {
      command: command.trim(),
      agents: REQUESTABLE_AGENTS.filter((a) => agents.includes(a)),
      language,
      aspect_ratio: aspect,
      require_approval: requireApproval,
      data_classification: classification,
      ...(budgetValue !== undefined ? { budget_limit_usd: budgetValue } : {}),
    };
    const payload = JSON.stringify(body);
    if (!attempt.current || attempt.current.payload !== payload) {
      attempt.current = { key: newIdempotencyKey(), payload };
    }

    setSubmitting(true);
    try {
      const job = await api<Job>("/v1/jobs", {
        method: "POST",
        body,
        headers: { "Idempotency-Key": attempt.current.key },
      });
      router.push(`/jobs/${job.id}`);
    } catch (err) {
      setError(toApiError(err));
      setSubmitting(false);
    }
  };

  const submitRef = useRef(submit);
  useEffect(() => {
    submitRef.current = submit;
  });
  useEffect(() => {
    if (countdown === null) return;
    const id = setTimeout(() => {
      if (countdown <= 1) {
        setCountdown(null);
        void submitRef.current();
      } else {
        setCountdown(countdown - 1);
      }
    }, 1000);
    return () => clearTimeout(id);
  }, [countdown]);

  return (
    <>
      <PageHeader title={t("workforce.title")} subtitle={t("workforce.subtitle")} />

      <form onSubmit={onSubmit} className="grid gap-4 lg:grid-cols-3" noValidate>
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <div className="mb-1.5 flex flex-wrap items-center justify-between gap-2">
              <label htmlFor="command" className="text-sm font-medium text-ink">
                {t("workforce.command")}
              </label>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant={voice.listening ? "danger" : "secondary"}
                  disabled={!voice.supported}
                  aria-pressed={voice.listening}
                  title={voice.supported ? undefined : t("voice.unsupported")}
                  onClick={voice.listening ? voice.stop : voice.start}
                  className={cx(voice.listening && "mic-live")}
                >
                  <MicIcon />
                  {voice.listening ? t("voice.stop") : t("voice.start")}
                </Button>
                <Button size="sm" onClick={() => setCommand(t("workforce.example"))}>
                  {t("workforce.useExample")}
                </Button>
              </div>
            </div>
            <textarea
              id="command"
              rows={8}
              value={command}
              maxLength={MAX}
              onChange={(e) => setCommand(e.target.value)}
              placeholder={t("workforce.commandPlaceholder")}
              aria-describedby="command-hint"
              aria-invalid={validation === "workforce.err.commandLength" || undefined}
              className="input min-h-40 resize-y leading-relaxed"
            />
            <div aria-live="polite" className="mt-1.5 min-h-5 text-xs">
              {countdown !== null && (
                <p role="alert" className="flex flex-wrap items-center gap-2 text-warn">
                  <span className="font-semibold">{t("voice.countdown", { n: countdown })}</span>
                  <Button size="sm" onClick={() => setCountdown(null)}>
                    {t("voice.cancel")}
                  </Button>
                </p>
              )}
              {voice.listening && (
                <p className="text-cyan">
                  {t("voice.listening")}
                  {voice.interim && <span className="text-dim"> — {voice.interim}</span>}
                  <span className="block text-dim">{t("voice.submitHint", { phrase: SUBMIT_PHRASES[language][0] })}</span>
                </p>
              )}
              {!voice.listening && voice.error && <p className="text-bad">{t(`voice.err.${voice.error}`)}</p>}
              {!voice.supported && <p className="text-dim">{t("voice.unsupported")}</p>}
              {voice.supported && !voice.listening && !voice.error && countdown === null && <p className="text-dim">{t("voice.privacy")}</p>}
            </div>
            <div id="command-hint" className="mt-1.5 flex justify-between gap-3 text-xs text-dim">
              <span>{t("workforce.commandHint")}</span>
              <span className={cx("tabular-nums", length > 0 && !lengthOk && "text-bad")} aria-live="polite">
                {length}/{MAX}
              </span>
            </div>
          </Card>

          <Card>
            <fieldset>
              <legend className="text-sm font-medium text-ink">{t("workforce.agents")}</legend>
              <p className="mb-3 mt-0.5 text-xs text-dim">{t("workforce.agentsHint")}</p>
              <div className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2">
                {REQUESTABLE_AGENTS.map((agent) => {
                  const on = agents.includes(agent);
                  return (
                    <button
                      key={agent}
                      type="button"
                      role="switch"
                      aria-checked={on}
                      aria-labelledby={`agent-${agent}-name`}
                      aria-describedby={`agent-${agent}-desc`}
                      onClick={() => toggleAgent(agent)}
                      className={cx(
                        "flex items-start gap-3 rounded-xl border p-3 text-left transition",
                        on ? "border-cyan/60 bg-brand/10" : "border-line bg-bg hover:border-brand-soft/50",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cx(
                          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-md border text-xs font-bold",
                          on ? "border-transparent bg-gradient-to-br from-brand to-cyan text-[#04121f]" : "border-line",
                        )}
                      >
                        {on ? "✓" : ""}
                      </span>
                      <span className="min-w-0">
                        <span id={`agent-${agent}-name`} className="block text-sm font-medium text-ink">
                          {t(`agent.${agent}`)}
                        </span>
                        <span id={`agent-${agent}-desc`} className="block text-xs text-dim">
                          {t(`agentDesc.${agent}`)}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            </fieldset>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <h2 className="mb-4 text-base font-semibold text-ink">{t("workforce.options")}</h2>
            <div className="space-y-4">
              <div>
                <label htmlFor="budget" className="label">
                  {t("workforce.budget")}
                </label>
                <input
                  id="budget"
                  type="number"
                  inputMode="decimal"
                  min="0"
                  step="0.01"
                  value={budget}
                  onChange={(e) => setBudget(e.target.value)}
                  aria-describedby="budget-hint"
                  aria-invalid={validation === "workforce.err.budget" || undefined}
                  className="input"
                />
                <p id="budget-hint" className="hint">
                  {t("workforce.budgetHint")}
                </p>
              </div>

              <div>
                <label htmlFor="language" className="label">
                  {t("workforce.language")}
                </label>
                <select id="language" value={language} onChange={(e) => setLanguage(e.target.value as Language)} className="input">
                  <option value="th">{t("lang.th")}</option>
                  <option value="en">{t("lang.en")}</option>
                </select>
              </div>

              <fieldset>
                <legend className="label">{t("workforce.aspect")}</legend>
                <div className="grid grid-cols-2 gap-2">
                  {(["16:9", "9:16"] as const).map((a) => (
                    <label
                      key={a}
                      className={cx(
                        "flex cursor-pointer items-center justify-center gap-2 rounded-lg border px-2 py-2 text-center text-sm transition has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-cyan",
                        aspect === a ? "border-cyan/60 bg-brand/10 text-ink" : "border-line bg-bg text-dim",
                      )}
                    >
                      <input
                        type="radio"
                        name="aspect"
                        value={a}
                        checked={aspect === a}
                        onChange={() => setAspect(a)}
                        className="sr-only"
                      />
                      <span
                        aria-hidden="true"
                        className={cx("shrink-0 rounded-sm border border-current", a === "16:9" ? "h-3 w-5" : "h-5 w-3")}
                      />
                      {t(`workforce.aspect.${a}`)}
                    </label>
                  ))}
                </div>
              </fieldset>

              <div>
                <label htmlFor="classification" className="label">
                  {t("workforce.classification")}
                </label>
                <select
                  id="classification"
                  value={classification}
                  onChange={(e) => setClassification(e.target.value as DataClassification)}
                  aria-describedby="classification-hint"
                  className="input"
                >
                  {DATA_CLASSIFICATIONS.map((c) => (
                    <option key={c} value={c}>
                      {t(`class.${c}`)}
                    </option>
                  ))}
                </select>
                <p id="classification-hint" className="hint">
                  {t(`classHint.${classification}`)}
                </p>
              </div>

              <Switch
                checked={requireApproval}
                onChange={setRequireApproval}
                label={t("workforce.requireApproval")}
                hint={t("workforce.requireApprovalHint")}
              />
            </div>
          </Card>

          {validation && (
            <p role="alert" className="rounded-xl border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-ink">
              {t(validation)}
            </p>
          )}
          {error && (
            <div>
              <ErrorState error={error} compact />
              <p className="hint">{t("workforce.retryHint")}</p>
            </div>
          )}

          <Button type="submit" variant="primary" loading={submitting} className="w-full">
            {submitting ? t("workforce.executing") : t("workforce.execute")}
          </Button>
        </div>
      </form>
    </>
  );
}

function MicIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
    </svg>
  );
}
