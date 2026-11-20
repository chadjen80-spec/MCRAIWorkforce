"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { CreateOrder } from "@/components/qtc/BillingParts";
import { LineEditor, QuotationStatusBadge, Totals, linesValid } from "@/components/qtc/QuotationParts";
import { Field, QtcError, QtcShell } from "@/components/qtc/QtcShell";
import { Badge, Button, Card, LoadingBlock, buttonClass, cx } from "@/components/ui";
import { useI18n } from "@/i18n/I18nProvider";
import type { MessageKey } from "@/i18n/dictionaries";
import { type ApiError, api, toApiError } from "@/lib/api";
import { type LineDraft, type Order, type Page, type Quotation, type Service, formatAmount, lineFromQuotation, linesBody, trimZeros } from "@/lib/qtc";
import { useApi } from "@/lib/useApi";

interface History {
  revisions: { revision: number; grand_total: string; reason: string; created_by: string; created_at: string }[];
  approvals: { revision: number; decision: string; comment: string; decided_by: string; decided_at: string }[];
}

type Form = "approve" | "send" | "accept" | "decline" | "revise" | "cancel" | null;

export default function QuotationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t, date, dateTime } = useI18n();
  const quotation = useApi<Quotation>(`qtc:quotation:${id}`, (signal) => api<Quotation>(`/v1/qtc/quotations/${id}`, { signal }));
  const history = useApi<History>(`qtc:quotation-history:${id}`, async (signal) => {
    const [revisions, approvals] = await Promise.all([
      api<History["revisions"]>(`/v1/qtc/quotations/${id}/revisions`, { signal }),
      api<History["approvals"]>(`/v1/qtc/quotations/${id}/approvals`, { signal }),
    ]);
    return { revisions, approvals };
  });
  const services = useApi<Page<Service>>("qtc:services-for-quote", (signal) => api<Page<Service>>("/v1/qtc/services", { query: { limit: 200 }, signal }));
  const accepted = quotation.data?.status === "accepted";
  const order = useApi<Page<Order>>(accepted ? `qtc:quotation-order:${id}` : null, (signal) =>
    api<Page<Order>>("/v1/qtc/orders", { query: { quotation_id: id, limit: 1 }, signal }),
  );
  const [editing, setEditing] = useState<LineDraft[] | null>(null);
  const [form, setForm] = useState<Form>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const q = quotation.data;

  const act = async (path: string, body?: unknown, method: "POST" | "PATCH" = "POST") => {
    setBusy(true);
    setError(null);
    try {
      await api(`/v1/qtc/quotations/${id}${path}`, { method, body });
      setForm(null);
      setEditing(null);
      quotation.reload();
      history.reload();
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <QtcShell>
      {quotation.error && !q ? (
        <QtcError error={quotation.error} onRetry={quotation.reload} />
      ) : !q ? (
        <LoadingBlock />
      ) : (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <Card>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-ink">{q.number}</h2>
              <span className="text-sm text-dim">{t("qtc.q.revision", { n: String(q.revision) })}</span>
              <QuotationStatusBadge status={q.status} />
            </div>
            <p className="font-medium text-ink">{q.title || "—"}</p>
            <p className="text-sm text-dim">
              {q.customer.code} · {q.customer.name} · {date(q.issue_date)} → {t("qtc.q.validUntil")} {date(q.valid_until)}
            </p>
            {q.status === "expired" && (
              <p role="status" className="mt-3 rounded-xl border border-bad/50 bg-bad/10 p-3 text-sm text-ink">
                {t("qtc.q.expiredHint")}
              </p>
            )}
            {q.missing_prices > 0 && (
              <p role="alert" className="mt-3 rounded-xl border border-warn/50 bg-warn/10 p-3 text-sm text-ink">
                {t("qtc.q.missing", { n: String(q.missing_prices) })}
              </p>
            )}
            {error && (
              <div className="mt-3">
                <QtcError error={error} />
              </div>
            )}

            {editing ? (
              <div className="mt-4 space-y-3">
                <LineEditor lines={editing} onChange={setEditing} services={services.data?.items ?? []} />
                <div className="flex gap-2">
                  <Button variant="primary" loading={busy} disabled={!linesValid(editing)} onClick={() => void act("", { lines: linesBody(editing) }, "PATCH")}>
                    {t("qtc.q.saveLines")}
                  </Button>
                  <Button onClick={() => setEditing(null)}>{t("qtc.cancel")}</Button>
                </div>
              </div>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[32rem] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-dim">
                      <th className="py-2 pr-2 font-medium">#</th>
                      <th className="py-2 pr-2 font-medium">{t("qtc.q.description")}</th>
                      <th className="py-2 pr-2 text-right font-medium">{t("qtc.q.qty")}</th>
                      <th className="py-2 pr-2 text-right font-medium">{t("qtc.q.price")}</th>
                      <th className="py-2 text-right font-medium">{t("qtc.q.total")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.lines.map((l) => (
                      <tr key={l.position} className={cx("border-b border-line/60 align-top", l.price_source === "missing" && "bg-warn/5")}>
                        <td className="py-2 pr-2 text-dim">{l.position}</td>
                        <td className="py-2 pr-2">
                          <span className="block text-ink">{l.description}</span>
                          <span className={cx("text-xs", l.price_source === "missing" ? "text-warn" : "text-dim")}>{t(`qtc.q.source.${l.price_source}` as MessageKey)}</span>
                          {l.discount_percent !== "0.0000" && <span className="text-xs text-dim"> · −{trimZeros(l.discount_percent)}%</span>}
                        </td>
                        <td className="py-2 pr-2 text-right tabular-nums">
                          {trimZeros(l.quantity)} {l.unit}
                        </td>
                        <td className="py-2 pr-2 text-right tabular-nums">{l.unit_price ? formatAmount(l.unit_price) : "—"}</td>
                        <td className="py-2 text-right tabular-nums">{formatAmount(q.prices_include_vat ? l.line_total : l.line_net)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="mt-4">
                  <Totals q={q} />
                </div>
              </div>
            )}
          </Card>

          <div className="space-y-4">
            <Card>
              <h3 className="mb-2 font-semibold text-ink">{t("qtc.q.download")}</h3>
              <div className="flex gap-2">
                <a className={buttonClass("secondary", "sm")} href={`/api/proxy/v1/qtc/quotations/${q.id}/document?format=pdf`}>
                  PDF
                </a>
                <a className={buttonClass("secondary", "sm")} href={`/api/proxy/v1/qtc/quotations/${q.id}/document?format=docx`}>
                  DOCX
                </a>
              </div>
              {["draft", "pending_approval"].includes(q.status) && <p className="mt-2 text-xs text-dim">{t("qtc.q.draftDoc")}</p>}
              {q.approved_by && <p className="mt-2 text-xs text-dim">{t("qtc.q.approvedBy", { who: q.approved_by })} · {dateTime(q.approved_at)}</p>}
              {q.sent_method && <p className="text-xs text-dim">{t("qtc.q.sentInfo", { method: t(`qtc.q.method.${q.sent_method}` as MessageKey) })} · {dateTime(q.sent_at)}</p>}
              {q.acceptance && (
                <p className="mt-1 text-xs text-ok">
                  {t("qtc.q.acceptedInfo", {
                    name: q.acceptance.accepted_by_name ?? "",
                    rev: String(q.accepted_revision ?? ""),
                    method: t(`qtc.q.accept.${q.acceptance.method}` as MessageKey),
                  })}
                  {q.acceptance.reference ? ` · ${q.acceptance.reference}` : ""}
                </p>
              )}
            </Card>

            <Actions q={q} form={form} setForm={setForm} busy={busy} act={act} startEdit={() => setEditing(q.lines.map(lineFromQuotation))} editing={!!editing} />

            {accepted && order.data && (order.data.items[0] ? (
              <Card>
                <p className="text-sm text-ink">{t("qtc.o.converted", { number: order.data.items[0].number })}</p>
                <Link className={cx(buttonClass("secondary", "sm"), "mt-2")} href={`/qtc/orders/${order.data.items[0].id}`}>
                  {order.data.items[0].number}
                </Link>
              </Card>
            ) : (
              <CreateOrder quotationId={q.id} />
            ))}

            {history.data && (history.data.revisions.length > 0 || history.data.approvals.length > 0) && (
              <Card>
                <h3 className="mb-2 font-semibold text-ink">{t("qtc.q.history")}</h3>
                <ol className="space-y-2 text-xs">
                  {history.data.revisions.map((r) => (
                    <li key={`r${r.revision}`} className="border-l-2 border-line pl-2">
                      <span className="font-medium text-ink">
                        {t("qtc.q.revision", { n: String(r.revision) })} · ฿{formatAmount(r.grand_total)}
                      </span>
                      <span className="block text-dim">
                        {r.created_by} · {dateTime(r.created_at)}
                      </span>
                      <a className="text-brand-soft hover:underline" href={`/api/proxy/v1/qtc/quotations/${q.id}/document?format=pdf&revision=${r.revision}`}>
                        PDF
                      </a>
                    </li>
                  ))}
                  {history.data.approvals.map((a, i) => (
                    <li key={`a${i}`} className="border-l-2 border-line pl-2">
                      <Badge tone={a.decision === "approved" ? "success" : "warn"}>
                        {a.decision === "approved" ? t("qtc.q.approve") : t("qtc.q.requestChanges")} · r{a.revision}
                      </Badge>
                      {a.comment && <span className="block text-ink">{a.comment}</span>}
                      <span className="block text-dim">
                        {a.decided_by} · {dateTime(a.decided_at)}
                      </span>
                    </li>
                  ))}
                </ol>
              </Card>
            )}
          </div>
        </div>
      )}
    </QtcShell>
  );
}

function Actions({
  q,
  form,
  setForm,
  busy,
  act,
  startEdit,
  editing,
}: {
  q: Quotation;
  form: Form;
  setForm: (f: Form) => void;
  busy: boolean;
  act: (path: string, body?: unknown) => Promise<void>;
  startEdit: () => void;
  editing: boolean;
}) {
  const { t } = useI18n();
  const [comment, setComment] = useState("");
  const [method, setMethod] = useState("email_by_user");
  const [acc, setAcc] = useState({ accepted_by_name: "", method: "purchase_order", reference: "", evidence_url: "" });
  const s = q.status;
  if (s === "accepted" || s === "cancelled") return null;

  return (
    <Card>
      <div className="flex flex-col gap-2">
        {s === "draft" && !editing && <Button onClick={startEdit}>{t("qtc.q.editLines")}</Button>}
        {s === "draft" && (
          <Button variant="primary" loading={busy} disabled={q.missing_prices > 0 || q.lines.length === 0 || editing} onClick={() => void act("/submit")}>
            {t("qtc.q.submit")}
          </Button>
        )}
        {s === "pending_approval" && (
          <>
            <p className="text-xs text-dim">{t("qtc.q.approveHint")}</p>
            <Field label={t("qtc.q.comment")} htmlFor="approve-comment">
              <textarea id="approve-comment" className="input" rows={2} maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
            </Field>
            <Button variant="primary" loading={busy} onClick={() => void act("/approve", { decision: "approved", comment })}>
              {t("qtc.q.approve")}
            </Button>
            <Button loading={busy} onClick={() => void act("/approve", { decision: "changes_requested", comment })}>
              {t("qtc.q.requestChanges")}
            </Button>
          </>
        )}
        {s === "approved" &&
          (form === "send" ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                void act("/send", { method, note: comment });
              }}
            >
              <p className="text-xs text-dim">{t("qtc.q.sendHint")}</p>
              <Field label={t("qtc.q.method")} htmlFor="send-method">
                <select id="send-method" className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
                  {["email_by_user", "line_by_user", "in_person", "printed", "other"].map((m) => (
                    <option key={m} value={m}>
                      {t(`qtc.q.method.${m}` as MessageKey)}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="flex gap-2">
                <Button type="submit" variant="primary" loading={busy}>
                  {t("qtc.save")}
                </Button>
                <Button onClick={() => setForm(null)}>{t("qtc.cancel")}</Button>
              </div>
            </form>
          ) : (
            <Button variant="primary" onClick={() => setForm("send")}>
              {t("qtc.q.send")}
            </Button>
          ))}
        {(s === "approved" || s === "sent") &&
          (form === "accept" ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                void act("/accept", { ...acc, evidence_url: acc.evidence_url || null, revision: q.revision });
              }}
            >
              <Field label={t("qtc.q.acceptedBy")} htmlFor="acc-name">
                <input id="acc-name" className="input" required maxLength={200} value={acc.accepted_by_name} onChange={(e) => setAcc({ ...acc, accepted_by_name: e.target.value })} />
              </Field>
              <Field label={t("qtc.q.acceptMethod")} htmlFor="acc-method">
                <select id="acc-method" className="input" value={acc.method} onChange={(e) => setAcc({ ...acc, method: e.target.value })}>
                  {["signed_document", "purchase_order", "email", "line", "verbal", "other"].map((m) => (
                    <option key={m} value={m}>
                      {t(`qtc.q.accept.${m}` as MessageKey)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label={t("qtc.q.reference")} htmlFor="acc-ref">
                <input id="acc-ref" className="input" maxLength={200} value={acc.reference} onChange={(e) => setAcc({ ...acc, reference: e.target.value })} />
              </Field>
              <Field label={t("qtc.q.evidenceUrl")} htmlFor="acc-url">
                <input id="acc-url" type="url" className="input" maxLength={1000} value={acc.evidence_url} onChange={(e) => setAcc({ ...acc, evidence_url: e.target.value })} />
              </Field>
              <div className="flex gap-2">
                <Button type="submit" variant="primary" loading={busy} disabled={!acc.accepted_by_name.trim()}>
                  {t("qtc.save")}
                </Button>
                <Button onClick={() => setForm(null)}>{t("qtc.cancel")}</Button>
              </div>
            </form>
          ) : (
            <Button onClick={() => setForm("accept")}>{t("qtc.q.accept")}</Button>
          ))}
        {s === "sent" && <Button onClick={() => void act("/decline", { reason: "" })}>{t("qtc.q.decline")}</Button>}
        {["pending_approval", "approved", "sent", "declined", "expired"].includes(s) &&
          (form === "revise" ? (
            <form
              className="space-y-2"
              onSubmit={(e) => {
                e.preventDefault();
                void act("/revise", { reason: comment });
              }}
            >
              <Field label={t("qtc.q.reason")} htmlFor="revise-reason">
                <input id="revise-reason" className="input" required maxLength={2000} value={comment} onChange={(e) => setComment(e.target.value)} />
              </Field>
              <div className="flex gap-2">
                <Button type="submit" variant="primary" loading={busy} disabled={!comment.trim()}>
                  {t("qtc.q.revise")}
                </Button>
                <Button onClick={() => setForm(null)}>{t("qtc.cancel")}</Button>
              </div>
            </form>
          ) : (
            <Button onClick={() => setForm("revise")}>{t("qtc.q.revise")}</Button>
          ))}
        {form !== "cancel" ? (
          <Button variant="ghost" onClick={() => setForm("cancel")}>
            {t("qtc.q.cancel")}
          </Button>
        ) : (
          <div className="flex gap-2">
            <Button variant="danger" loading={busy} onClick={() => void act("/cancel", { reason: "" })}>
              {t("qtc.q.cancel")}
            </Button>
            <Button onClick={() => setForm(null)}>{t("qtc.cancel")}</Button>
          </div>
        )}
      </div>
    </Card>
  );
}
