"""/v1/qtc/assistant: Mira's sales and finance commands, routed by structured intent rules.

`POST /assistant` classifies the text into one intent and answers through a QTC agent. Questions (pending
approvals, who has not paid, receivables) answer straight away from exact queries. Anything that writes
(a draft quotation, reminder drafts) comes back as a proposal with `confirm`; it runs only when a person
confirms through `POST /assistant/confirm`. Text that matches no intent returns `intent: null`, so Mira's
existing creative-job flow handles it unchanged.

Reminder drafts are approved by an owner or admin. Sending is not available at all:
`POST /reminders/{id}/send` returns 403 until consent, scoped credentials and sending integrations exist.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.registry import all_agents
from app.db import get_db, utcnow
from app.deps import Principal, get_principal, require_admin
from app.errors import AppError, not_found
from app.qtc import audit
from app.qtc import schemas as s
from app.qtc.agents import CollectionAgent, FinanceAgent, QtcAgentContext, QuotationAgent, SalesIntakeAgent
from app.qtc.api import require_user
from app.qtc.models import Invoice, PaymentReminder
from app.qtc.quotation_api import create_quotation

router = APIRouter(prefix="/v1/qtc", tags=["quote-to-cash"])

Intent = Literal["create_quotation", "pending_quotations", "unpaid_customers", "receivables_summary", "collection_drafts"]

# Order matters: the first rule that matches wins. A quotation request must name a customer ("ลูกค้า" / "customer")
# so that creative requests such as "quotation PDF for ..." keep going to the Document Agent.
RULES: list[tuple[Intent, re.Pattern[str]]] = [
    (
        "collection_drafts",
        re.compile(
            r"(ติดตาม|ทวง|เตือน).{0,12}(เงิน|ชำระ|หนี้)|follow[- ]?up.{0,30}(overdue|payment|invoice)|"
            r"(payment|collection) reminders?|remind.{0,30}overdue",
            re.I,
        ),
    ),
    (
        "pending_quotations",
        re.compile(
            r"ใบเสนอราคา.{0,25}(ยังไม่|รอ|ค้าง).{0,6}อนุมัติ|quot(ation|e)s?.{0,30}(waiting|pending|awaiting|not (yet )?approved)|"
            r"unapproved quot",
            re.I,
        ),
    ),
    (
        "unpaid_customers",
        re.compile(
            r"ลูกค้า.{0,20}(ยังไม่|ไม่ได้|ค้าง).{0,8}(จ่าย|ชำระ)|ใคร.{0,10}(ยังไม่|ค้าง).{0,6}(จ่าย|ชำระ)|"
            r"who (hasn'?t|has not|haven'?t|have not) paid|unpaid customers|customers?.{0,20}(not|haven'?t) paid",
            re.I,
        ),
    ),
    (
        "receivables_summary",
        re.compile(
            r"(สรุป|ยอด|รายงาน).{0,12}ลูกหนี้|ลูกหนี้.{0,12}(เดือนนี้|สรุป)|receivables?.{0,20}(summary|this month|report)|"
            r"summar(y|ise|ize).{0,20}receivables",
            re.I,
        ),
    ),
    (
        "create_quotation",
        re.compile(
            r"(สร้าง|ทำ|ออก|เตรียม).{0,8}ใบเสนอราคา.{0,80}(ลูกค้า|customer)|"
            r"(create|make|prepare|draft).{0,10}quot(ation|e).{0,80}(customer|for [A-Z])",
            re.I,
        ),
    ),
]


def classify(text: str) -> Intent | None:
    for intent, pattern in RULES:
        if pattern.search(text):
            return intent
    return None


class AssistantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    language: Literal["th", "en"] = "th"
    use_ai: bool = False  # optional AI extraction (costs money); answers to questions never use AI


class AssistantOut(BaseModel):
    intent: Intent | None
    agent: str | None = None
    reply: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    confirm: dict[str, Any] | None = None  # present when the action needs a person's confirmation


@router.post("/assistant", response_model=AssistantOut)
def assistant(body: AssistantIn, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    intent = classify(body.text)
    if intent is None:
        return AssistantOut(intent=None)
    ctx = QtcAgentContext(session=session, org=principal.organization, actor=principal.actor, language=body.language, use_ai=body.use_ai)
    if intent == "create_quotation":
        out = QuotationAgent(ctx).propose(body.text)
    elif intent == "pending_quotations":
        out = FinanceAgent(ctx).pending_quotations()
    elif intent == "unpaid_customers":
        out = FinanceAgent(ctx).unpaid_customers()
    elif intent == "receivables_summary":
        out = FinanceAgent(ctx).receivables_summary()
    else:
        out = CollectionAgent(ctx).propose()
    session.commit()  # usage records of any AI call
    confirm = out.pop("confirm", None)
    return AssistantOut(intent=intent, agent=out.pop("agent", None), reply=out.pop("reply", ""), data=out, confirm=confirm)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["create_quotation", "draft_reminders"]
    payload: dict[str, Any]
    language: Literal["th", "en"] = "th"
    idempotency_key: str | None = Field(default=None, max_length=128)


@router.post("/assistant/confirm", response_model=AssistantOut)
def assistant_confirm(body: ConfirmIn, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    """A person confirmed Mira's proposal. Runs the internal action (nothing is sent outside)."""
    ctx = QtcAgentContext(session=session, org=principal.organization, actor=principal.actor, language=body.language)
    if body.action == "create_quotation":
        p = body.payload
        lines = [s.QuotationLineIn(service_id=x.get("service_id"), description=x.get("description") or "") for x in p.get("lines", [])]
        q = create_quotation(
            body=s.QuotationCreate(
                customer_id=uuid.UUID(str(p["customer_id"])), brief=str(p.get("brief", ""))[:4000], lines=lines, match_brief=False
            ),
            response=Response(),
            principal=principal,
            session=session,
            idempotency_key=body.idempotency_key,
        )
        reply = ctx.t(
            f"สร้างใบเสนอราคาฉบับร่าง {q.number} แล้วค่ะ" + (f" ยังมี {q.missing_prices} รายการที่ต้องกำหนดราคา" if q.missing_prices else ""),
            f"Draft quotation {q.number} created" + (f"; {q.missing_prices} line(s) still need a price." if q.missing_prices else "."),
        )
        return AssistantOut(
            intent="create_quotation",
            agent="quotation",
            reply=reply,
            data={"quotation_id": str(q.id), "number": q.number, "link": f"/qtc/quotations/{q.id}"},
        )
    ids = [uuid.UUID(str(x)) for x in body.payload.get("invoice_ids", [])]
    drafts = CollectionAgent(ctx).draft(ids)
    session.commit()
    reply = ctx.t(
        f"เตรียมร่างข้อความติดตามเงิน {len(drafts)} ฉบับแล้ว กรุณาตรวจและอนุมัติก่อน ระบบไม่ส่งข้อความเอง",
        f"{len(drafts)} reminder draft(s) are ready. Review and approve them; the system does not send them.",
    )
    return AssistantOut(
        intent="collection_drafts",
        agent="collection",
        reply=reply,
        data={"reminder_ids": [str(r.id) for r in drafts], "link": "/qtc/invoices?status=overdue"},
    )


# ---- reminders ----------------------------------------------------------------------------------------
class ReminderOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_number: str | None
    channel: str
    status: str
    body: str
    created_by: str
    approved_by: str | None


def _reminder_out(session: Session, r: PaymentReminder) -> ReminderOut:
    inv = session.get(Invoice, r.invoice_id)
    return ReminderOut(
        id=r.id,
        invoice_id=r.invoice_id,
        invoice_number=inv.number if inv else None,
        channel=r.channel,
        status=r.status,
        body=r.body,
        created_by=r.created_by,
        approved_by=r.approved_by,
    )


def _reminder(session: Session, org_id: uuid.UUID, rid: uuid.UUID) -> PaymentReminder:
    r = session.scalar(select(PaymentReminder).where(PaymentReminder.id == rid, PaymentReminder.organization_id == org_id).with_for_update())
    if r is None:
        raise not_found("Reminder")
    return r


@router.get("/reminders", response_model=list[ReminderOut])
def list_reminders(
    status: str | None = Query(default=None, pattern="^(draft|approved|sent|cancelled)$"),
    invoice_id: uuid.UUID | None = None,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    where: list[Any] = [PaymentReminder.organization_id == principal.organization.id]
    if status:
        where.append(PaymentReminder.status == status)
    if invoice_id:
        where.append(PaymentReminder.invoice_id == invoice_id)
    rows = session.scalars(select(PaymentReminder).where(*where).order_by(PaymentReminder.created_at.desc()).limit(100)).all()
    return [_reminder_out(session, r) for r in rows]


class ReminderEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)


@router.patch("/reminders/{rid}", response_model=ReminderOut)
def edit_reminder(rid: uuid.UUID, body: ReminderEdit, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    r = _reminder(session, principal.organization.id, rid)
    if r.status != "draft":
        raise AppError(409, "INVALID_STATE", f"The reminder is {r.status}")
    r.body = body.body
    audit.record(session, principal.organization.id, "reminder", r.id, "reminder.edited", principal.actor)
    session.commit()
    return _reminder_out(session, r)


@router.post("/reminders/{rid}/approve", response_model=ReminderOut)
def approve_reminder(rid: uuid.UUID, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    r = _reminder(session, principal.organization.id, rid)
    if r.status != "draft":
        raise AppError(409, "INVALID_STATE", f"The reminder is {r.status}")
    r.status, r.approved_by = "approved", principal.actor
    audit.record(session, principal.organization.id, "reminder", r.id, "reminder.approved", principal.actor, {"channel": r.channel})
    session.commit()
    return _reminder_out(session, r)


@router.post("/reminders/{rid}/cancel", response_model=ReminderOut)
def cancel_reminder(rid: uuid.UUID, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    r = _reminder(session, principal.organization.id, rid)
    if r.status in ("sent", "cancelled"):
        raise AppError(409, "INVALID_STATE", f"The reminder is {r.status}")
    r.status = "cancelled"
    audit.record(session, principal.organization.id, "reminder", r.id, "reminder.cancelled", principal.actor)
    session.commit()
    return _reminder_out(session, r)


@router.post("/reminders/{rid}/send")
def send_reminder(rid: uuid.UUID, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    _reminder(session, principal.organization.id, rid)
    raise AppError(
        403,
        "EXTERNAL_SENDING_DISABLED",
        "The system does not send messages to customers. Copy the approved text and send it yourself, then record it.",
    )


# ---- agents (read-only helpers for integrations) ---------------------------------------------------------
class IntakeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    use_ai: bool = False


@router.post("/agents/sales-intake")
def sales_intake(body: IntakeIn, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)) -> dict[str, Any]:
    """Read an inquiry (e.g. forwarded by n8n): matched customer or new-customer details, and requested services.
    Read-only: nothing is created."""
    ctx = QtcAgentContext(session=session, org=principal.organization, actor=principal.actor, use_ai=body.use_ai)
    out = SalesIntakeAgent(ctx).run(body.text)
    audit.record(
        session,
        principal.organization.id,
        "agent",
        principal.organization.id,
        "agent.sales_intake",
        principal.actor,
        {
            "matched_customer": (out["customer"] or {}).get("code"),
            "services": [x["code"] for x in out["services"]],
            "used_ai": out["used_ai"],
            "at": utcnow(),
        },
    )
    session.commit()
    return out


@router.get("/agents")
def list_agents(principal: Principal = Depends(get_principal)) -> list[dict[str, Any]]:
    return [{"name": a.name, "family": a.family, "description": a.description, "queue": a.queue} for a in all_agents()]
