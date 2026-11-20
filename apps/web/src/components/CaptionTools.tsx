"use client";

// Caption controls: every action asks the AI for a complete rewrite of the caption (never appended or trimmed
// text). The current caption stays on screen and in storage until the new version has been written and has
// passed the checks; earlier versions are kept and can be restored.

import { useState } from "react";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { api, toApiError } from "@/lib/api";
import type { Artifact } from "@/lib/types";
import { Button, cx } from "./ui";

export type CaptionAction = "regenerate" | "rewrite" | "tone" | "shorten" | "expand";
export const CAPTION_TONES = ["conversational", "friendly", "warm", "playful", "professional", "confident"] as const;

const SOURCES = ["original", "edit", "regenerate", "rewrite", "tone", "shorten", "expand"];

export interface CaptionVersion {
  text: string;
  at: string;
  by: string;
  source: string;
}

export function captionVersions(artifact: Artifact): CaptionVersion[] {
  const raw = artifact.metadata?.versions;
  return Array.isArray(raw) ? (raw.filter((v) => v && typeof v === "object" && typeof (v as CaptionVersion).text === "string") as CaptionVersion[]) : [];
}

const ACTIONS: { action: CaptionAction; label: MessageKey }[] = [
  { action: "regenerate", label: "cap.regenerate" },
  { action: "rewrite", label: "cap.rewrite" },
  { action: "tone", label: "cap.tone" },
  { action: "shorten", label: "cap.shorten" },
  { action: "expand", label: "cap.expand" },
];

export function CaptionTools({ artifact, onUpdated }: { artifact: Artifact; onUpdated: (updated: Artifact) => void }) {
  const { t, dateTime } = useI18n();
  const [open, setOpen] = useState<CaptionAction | null>(null);
  const [busy, setBusy] = useState<CaptionAction | "restore" | null>(null);
  const [feedback, setFeedback] = useState("");
  const [tone, setTone] = useState<(typeof CAPTION_TONES)[number]>("friendly");
  const [error, setError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const versions = captionVersions(artifact);

  const run = async (action: CaptionAction) => {
    setBusy(action);
    setError(null);
    setWarnings([]);
    try {
      const res = await api<{ artifact: Artifact; warnings: string[] }>(`/v1/artifacts/${artifact.id}/regenerate-caption`, {
        method: "POST",
        body: { action, instruction: feedback.trim() || null, tone: action === "tone" ? tone : null },
      });
      setWarnings(res.warnings);
      setFeedback("");
      setOpen(null);
      onUpdated(res.artifact);
    } catch (e) {
      // The caption on screen is the one still stored: nothing was replaced.
      setError(t("cap.failed", { message: toApiError(e).message }));
    } finally {
      setBusy(null);
    }
  };

  const restore = async (v: CaptionVersion) => {
    setBusy("restore");
    setError(null);
    try {
      onUpdated(await api<Artifact>(`/v1/artifacts/${artifact.id}/text`, { method: "PUT", body: { text: v.text } }));
      setShowHistory(false);
    } catch (e) {
      setError(t("cap.restoreFailed", { message: toApiError(e).message }));
    } finally {
      setBusy(null);
    }
  };

  const choose = (action: CaptionAction) => {
    setError(null);
    // Regenerate and Change tone take an optional note / a tone first; the others run straight away.
    if (action === "regenerate" || action === "tone") setOpen(open === action ? null : action);
    else void run(action);
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("cap.actions")}>
        {ACTIONS.map(({ action, label }) => (
          <Button
            key={action}
            size="sm"
            variant={open === action ? "primary" : "secondary"}
            loading={busy === action}
            disabled={busy !== null && busy !== action}
            aria-pressed={open === action}
            onClick={() => choose(action)}
          >
            {busy === action ? t("cap.working") : t(label)}
          </Button>
        ))}
        {versions.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setShowHistory((x) => !x)} aria-expanded={showHistory} disabled={busy !== null}>
            {t("cap.history", { n: versions.length })}
          </Button>
        )}
      </div>

      {open && (
        <div className="space-y-2 rounded-lg border border-line bg-bg p-3">
          {open === "tone" && (
            <>
              <label htmlFor={`tone-${artifact.id}`} className="label">
                {t("cap.toneLabel")}
              </label>
              <select id={`tone-${artifact.id}`} value={tone} onChange={(e) => setTone(e.target.value as typeof tone)} className="input">
                {CAPTION_TONES.map((x) => (
                  <option key={x} value={x}>
                    {t(`cap.tones.${x}` as MessageKey)}
                  </option>
                ))}
              </select>
            </>
          )}
          <label htmlFor={`feedback-${artifact.id}`} className="label">
            {t("cap.feedbackLabel")}
          </label>
          <textarea
            id={`feedback-${artifact.id}`}
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            maxLength={500}
            rows={2}
            placeholder={t("cap.feedbackPlaceholder")}
            className="input resize-y"
          />
          <p className="text-xs text-dim">{t(open === "tone" ? "cap.toneNote" : "cap.regenerateNote")}</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" loading={busy === open} onClick={() => void run(open)}>
              {t(open === "tone" ? "cap.toneStart" : "cap.regenerateStart")}
            </Button>
            <Button size="sm" onClick={() => setOpen(null)} disabled={busy !== null}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      )}

      {busy && busy !== "restore" && (
        <p role="status" className="text-xs text-dim">
          {t("cap.keepNote")}
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-bad">
          {error}
        </p>
      )}
      {warnings.length > 0 && (
        <div role="status" className="rounded-lg border border-warn/50 bg-warn/10 p-2 text-xs text-ink">
          <p className="font-medium text-warn">{t("capRegen.warnings")}</p>
          <ul className="ml-4 list-disc">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {showHistory && (
        <ol className="space-y-2 rounded-lg border border-line bg-bg p-3" aria-label={t("cap.historyTitle")}>
          {[...versions].reverse().map((v, i) => (
            <li key={`${v.at}-${i}`} className={cx("space-y-1", i > 0 && "border-t border-line pt-2")}>
              <p className="text-xs text-dim">
                {SOURCES.includes(v.source) ? t(`cap.source.${v.source}` as MessageKey) : v.source}
                {" · "}
                {dateTime(v.at)}
              </p>
              <p className="line-clamp-3 whitespace-pre-wrap text-sm text-ink">{v.text}</p>
              <Button size="sm" variant="ghost" loading={busy === "restore"} onClick={() => void restore(v)}>
                {t("cap.restore")}
              </Button>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
