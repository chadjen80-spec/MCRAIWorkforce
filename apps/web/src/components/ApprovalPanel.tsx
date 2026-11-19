"use client";

import { useState } from "react";
import { Button, Card, ErrorState } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import { ApiError, api, toApiError } from "@/lib/api";
import type { ApprovalRequest, Job } from "@/lib/types";

/** Approve or reject a job awaiting approval (owner/admin only; others see the API's error). */
export function ApprovalPanel({ jobId, onDone }: { jobId: string; onDone: () => void }) {
  const { t } = useI18n();
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState<ApprovalRequest["decision"] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const decide = async (decision: ApprovalRequest["decision"]) => {
    setPending(decision);
    setError(null);
    const body: ApprovalRequest = { decision, ...(comment.trim() ? { comment: comment.trim() } : {}) };
    try {
      await api<Job>(`/v1/jobs/${encodeURIComponent(jobId)}/approve`, { method: "POST", body });
    } catch (e) {
      setError(toApiError(e));
    }
    setPending(null);
    // Reload either way: on 409 the job has already moved on and the page should show that.
    onDone();
  };

  return (
    <Card className="border-warn/50">
      <h2 className="text-base font-semibold text-ink">{t("monitor.approvalTitle")}</h2>
      <p className="mt-1 text-sm text-dim">{t("monitor.approvalDesc")}</p>
      <label htmlFor="approval-comment" className="label mt-4">
        {t("monitor.comment")}
      </label>
      <textarea
        id="approval-comment"
        rows={2}
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        placeholder={t("monitor.commentPlaceholder")}
        className="input"
      />
      {error && (
        <div className="mt-3">
          <ErrorState error={error} compact />
        </div>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="primary" loading={pending === "approved"} disabled={pending !== null} onClick={() => decide("approved")}>
          {t("monitor.approve")}
        </Button>
        <Button variant="danger" loading={pending === "rejected"} disabled={pending !== null} onClick={() => decide("rejected")}>
          {t("monitor.reject")}
        </Button>
      </div>
    </Card>
  );
}
