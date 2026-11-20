"""Quote-to-Cash agents: SalesIntake, Quotation, Finance and Collection.

Shared rules
- Structured outputs only: every AI answer is parsed into a Pydantic schema (`complete_json`) and validated again
  here; anything that fails validation is discarded and a deterministic fallback is used.
- No commercial number comes from an AI. Prices come from the approved price list or a person; balances, totals
  and counts come from database queries. The schemas the AI fills have no price or amount fields at all.
- Agents never create or change records by themselves: they return proposals. Mira (or the API caller) shows the
  proposal and a person confirms it (see `app.qtc.assistant_api`).
- Cost tracking and budget: AI calls use the organization's OpenAI provider, are refused when the monthly budget
  is used up, and are recorded as `UsageEvent`s (agent = the QTC agent's name). Without a provider the agents work
  deterministically.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.registry import AgentSpec, register
from app.config import get_settings
from app.errors import AgentError
from app.models import Organization, UsageEvent
from app.providers.config import resolve_openai
from app.qtc import audit
from app.qtc import quotations as engine
from app.qtc.models import (
    Customer,
    CustomerContact,
    Invoice,
    PaymentRecord,
    PaymentReminder,
    PriceListItem,
    Quotation,
)
from app.qtc.money import ZERO
from app.services.budget import committed_this_month
from app.services.router import make_openai

T = TypeVar("T", bound=BaseModel)
OPEN_INVOICE = ("issued", "partially_paid")


@dataclass
class QtcAgentContext:
    session: Session
    org: Organization
    actor: str
    language: str = "th"
    use_ai: bool = False

    def llm(self, agent: str, system: str, user: str, schema: type[T]) -> T | None:
        """One structured AI call, or None when AI is off, not configured, over budget or invalid."""
        if not self.use_ai:
            return None
        cfg = resolve_openai(self.session, self.org.id)
        if cfg is None:
            return None
        if committed_this_month(self.session, self.org.id) + get_settings().llm_estimated_usd_per_call > self.org.monthly_budget_usd:
            return None
        try:
            out, usage = make_openai(cfg).complete_json(system, user, schema, temperature=0.2)
        except AgentError:
            return None
        self.session.add(
            UsageEvent(
                organization_id=self.org.id,
                agent=agent,
                provider="openai",
                operation="chat",
                units={"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "purpose": agent},
                cost_usd=usage.cost_usd,
            )
        )
        return out

    def t(self, th: str, en: str) -> str:
        return th if self.language == "th" else en


def _money(d: Decimal) -> str:
    return f"{d:,.2f}"


# ---- SalesIntakeAgent ------------------------------------------------------------------------------
class IntakeModel(BaseModel):
    """What the AI may extract from an inquiry. No prices or amounts by design."""

    customer_name: str = Field(default="", max_length=200)
    contact_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    requested_items: list[str] = Field(default_factory=list, max_length=20)


class SalesIntakeAgent:
    name = "sales_intake"

    def __init__(self, ctx: QtcAgentContext):
        self.ctx = ctx

    def run(self, text: str) -> dict[str, Any]:
        """Read an inquiry: who is asking (existing customer or a new one to create) and what they ask for."""
        s, org_id = self.ctx.session, self.ctx.org.id
        ai = self.ctx.llm(
            self.name,
            "Extract the customer and the requested services from a Thai or English sales inquiry. Never output prices or amounts.",
            text[:4000],
            IntakeModel,
        )
        email = (ai.email if ai else "") or next(iter(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)), "")
        phone = (ai.phone if ai else "") or next(iter(re.findall(r"(?:\+66|0)\d[\d -]{7,11}\d", text)), "")
        # Labelled lines, as the n8n inquiry workflow and most web forms send them ("บริษัท: …", "Name: …").
        company = _field(text, r"บริษัท|company|organi[sz]ation")
        contact = _field(text, r"ผู้ติดต่อ|contact|name|ชื่อ")
        customer = find_customer(s, org_id, text, (ai.customer_name if ai else "") or company)
        services = [
            {"service_id": str(svc.id), "code": svc.code, "name": svc.name, "matched": word} for svc, word in engine.match_brief(s, org_id, text)
        ]
        extra = [
            x.strip()[:200]
            for x in (ai.requested_items if ai else [])
            if x.strip() and not any(x.strip().lower() in (m["name"].lower(), m["code"].lower()) for m in services)
        ]
        return {
            "agent": self.name,
            "customer": customer_brief(customer) if isinstance(customer, Customer) else None,
            "customer_candidates": [customer_brief(c) for c in customer] if isinstance(customer, list) else [],
            "new_customer": None
            if customer
            else {
                "name": (ai.customer_name if ai else "") or company,
                "email": email,
                "phone": phone,
                "contact_name": (ai.contact_name if ai else "") or contact,
            },
            "services": services,
            "unlisted_items": extra,
            "used_ai": ai is not None,
        }


def _field(text: str, label: str) -> str:
    m = re.search(rf"^\s*(?:{label})\s*[:：]\s*(.+?)\s*$", text, re.I | re.M)
    return m.group(1)[:200] if m else ""


def customer_brief(c: Customer) -> dict[str, str]:
    return {"id": str(c.id), "code": c.code, "name": c.name}


def find_customer(session: Session, org_id: uuid.UUID, text: str, hint: str = "") -> Customer | list[Customer] | None:
    """An active customer named (or coded) in the text; a list when several match; None when none does."""
    customers = list(session.scalars(select(Customer).where(Customer.organization_id == org_id, Customer.status == "active")))
    low = text.lower()
    hits = [
        c
        for c in customers
        if c.code.lower() in re.findall(r"[a-z0-9._-]+", low)
        or (len(c.name) >= 2 and c.name.lower() in low)
        or (c.legal_name and len(c.legal_name) >= 2 and c.legal_name.lower() in low)
    ]
    if not hits and hint.strip():
        hits = [c for c in customers if hint.strip().lower() in c.name.lower()]
    if not hits:
        # a short name after "ลูกค้า" / "customer", e.g. "ให้ลูกค้า ABC"
        m = re.search(r"(?:ลูกค้า|customer)\s*([^\s,]{2,40})", text, re.I)
        if m:
            word = m.group(1).lower()
            hits = [c for c in customers if word in c.name.lower() or word == c.code.lower()]
    if len(hits) == 1:
        return hits[0]
    return hits or None


# ---- QuotationAgent ----------------------------------------------------------------------------------
class ExtraItems(BaseModel):
    """Requested items that are not in the catalogue. Descriptions only: never prices."""

    items: list[str] = Field(default_factory=list, max_length=20)


class QuotationAgent:
    name = "quotation"

    def __init__(self, ctx: QtcAgentContext):
        self.ctx = ctx

    def propose(self, text: str) -> dict[str, Any]:
        """A draft-quotation proposal from a spoken or typed request. Prices: approved list or 'needs a price'."""
        s, org_id, t = self.ctx.session, self.ctx.org.id, self.ctx.t
        found = find_customer(s, org_id, text)
        if found is None:
            return {
                "agent": self.name,
                "ok": False,
                "reply": t(
                    "ไม่พบลูกค้าที่กล่าวถึง กรุณาเพิ่มลูกค้าที่หน้าลูกค้า หรือบอกชื่อลูกค้าให้ตรงกับที่บันทึกไว้",
                    "I could not find that customer. Add them under Customers, or say the name as it is saved.",
                ),
                "link": "/qtc/customers",
            }
        if isinstance(found, list):
            names = ", ".join(c.name for c in found[:5])
            return {
                "agent": self.name,
                "ok": False,
                "candidates": [customer_brief(c) for c in found],
                "reply": t(f"พบลูกค้าหลายรายที่ตรงกัน: {names} กรุณาระบุให้ชัดขึ้น", f"Several customers match: {names}. Please be more specific."),
            }
        try:
            pl = engine.default_price_list(s, org_id)
        except Exception:  # noqa: BLE001 - reported as a plain answer
            return {
                "agent": self.name,
                "ok": False,
                "link": "/qtc/services",
                "reply": t(
                    "ยังไม่มีรายการราคาที่อนุมัติ กรุณาอนุมัติรายการราคาก่อนทำใบเสนอราคา",
                    "There is no approved price list yet. Approve one before creating quotations.",
                ),
            }
        matches = engine.match_brief(s, org_id, text)
        prices = {
            sid: p for sid, p in s.execute(select(PriceListItem.service_id, PriceListItem.unit_price).where(PriceListItem.price_list_id == pl.id))
        }
        lines: list[dict[str, Any]] = [
            {
                "service_id": str(svc.id),
                "description": svc.name,
                "unit": svc.unit,
                "approved_price": format(prices[svc.id], "f") if svc.id in prices else None,
            }
            for svc, _ in matches
        ]
        ai = self.ctx.llm(
            self.name,
            "List the items the customer asks for that are NOT in this catalogue. Descriptions only, no prices.",
            f"Catalogue: {[svc.name for svc, _ in matches]}\nRequest: {text[:3000]}",
            ExtraItems,
        )
        for item in ai.items if ai else []:
            if item.strip() and not any(item.strip().lower() == x["description"].lower() for x in lines):
                lines.append({"service_id": None, "description": item.strip()[:200], "unit": "งาน", "approved_price": None})
        missing = sum(1 for x in lines if x["approved_price"] is None)
        customer: Customer = found
        reply = t(
            f"เตรียมใบเสนอราคาฉบับร่างให้ลูกค้า {customer.name} {len(lines)} รายการ"
            + (f" มี {missing} รายการที่ต้องกำหนดราคา" if missing else "")
            + " ต้องการให้สร้างฉบับร่างไหมคะ",
            f"I prepared a draft quotation for {customer.name} with {len(lines)} line(s)"
            + (f"; {missing} need a price" if missing else "")
            + ". Shall I create the draft?",
        )
        return {
            "agent": self.name,
            "ok": True,
            "reply": reply,
            "customer": customer_brief(customer),
            "lines": lines,
            "missing_prices": missing,
            "used_ai": ai is not None,
            "confirm": {
                "action": "create_quotation",
                "payload": {
                    "customer_id": str(customer.id),
                    "brief": text[:4000],
                    "lines": [{"service_id": x["service_id"], "description": x["description"]} for x in lines],
                },
            },
        }


# ---- FinanceAgent ------------------------------------------------------------------------------------
class FinanceAgent:
    """Answers from exact queries only (no AI): approvals waiting, who has not paid, receivables this month."""

    name = "finance"

    def __init__(self, ctx: QtcAgentContext):
        self.ctx = ctx

    def pending_quotations(self) -> dict[str, Any]:
        s, t = self.ctx.session, self.ctx.t
        rows = s.scalars(
            select(Quotation)
            .where(Quotation.organization_id == self.ctx.org.id, Quotation.status == "pending_approval")
            .order_by(Quotation.created_at)
        ).all()
        items = [
            {"id": str(q.id), "number": q.number, "title": q.title, "total": format(q.grand_total, "f"), "link": f"/qtc/quotations/{q.id}"}
            for q in rows
        ]
        total = sum((q.grand_total for q in rows), ZERO)
        reply = (
            t(
                f"มีใบเสนอราคารออนุมัติ {len(rows)} ฉบับ รวม {_money(total)} บาท",
                f"{len(rows)} quotation(s) wait for approval, {_money(total)} baht in total.",
            )
            if rows
            else t("ไม่มีใบเสนอราคาที่รออนุมัติค่ะ", "No quotation is waiting for approval.")
        )
        return {"agent": self.name, "reply": reply, "items": items, "link": "/qtc/quotations"}

    def unpaid_customers(self) -> dict[str, Any]:
        s, t, today = self.ctx.session, self.ctx.t, date.today()
        invoices = s.scalars(select(Invoice).where(Invoice.organization_id == self.ctx.org.id, Invoice.status.in_(OPEN_INVOICE))).all()
        per: dict[uuid.UUID, dict[str, Any]] = {}
        for i in invoices:
            row = per.setdefault(i.customer_id, {"outstanding": ZERO, "overdue": ZERO, "invoices": 0})
            row["outstanding"] += i.total - i.paid_amount
            row["invoices"] += 1
            if i.due_date and i.due_date < today:
                row["overdue"] += i.total - i.paid_amount
        customers = {c.id: c for c in s.scalars(select(Customer).where(Customer.id.in_(per)))} if per else {}
        items = sorted(
            (
                {
                    "customer": customers[cid].name,
                    "code": customers[cid].code,
                    "outstanding": format(v["outstanding"], "f"),
                    "overdue": format(v["overdue"], "f"),
                    "invoices": v["invoices"],
                }
                for cid, v in per.items()
            ),
            key=lambda x: -Decimal(x["outstanding"]),
        )
        if not items:
            return {
                "agent": self.name,
                "reply": t("ทุกรายชำระครบแล้วค่ะ ไม่มียอดค้างชำระ", "Everyone has paid; nothing is outstanding."),
                "items": [],
                "link": "/qtc/invoices",
            }
        top = ", ".join(f"{x['customer']} {_money(Decimal(x['outstanding']))} บาท" for x in items[:3])
        reply = t(
            f"มีลูกค้าค้างชำระ {len(items)} ราย ได้แก่ {top}",
            f"{len(items)} customer(s) have not paid in full: "
            + ", ".join(f"{x['customer']} {_money(Decimal(x['outstanding']))} baht" for x in items[:3]),
        )
        return {"agent": self.name, "reply": reply, "items": items, "link": "/qtc/invoices?status=open"}

    def receivables_summary(self) -> dict[str, Any]:
        s, t, org_id, today = self.ctx.session, self.ctx.t, self.ctx.org.id, date.today()
        month_start = today.replace(day=1)
        open_rows = s.scalars(select(Invoice).where(Invoice.organization_id == org_id, Invoice.status.in_(OPEN_INVOICE))).all()
        outstanding = sum((i.total - i.paid_amount for i in open_rows), ZERO)
        overdue = sum((i.total - i.paid_amount for i in open_rows if i.due_date and i.due_date < today), ZERO)
        invoiced = (
            s.scalar(
                select(func.coalesce(func.sum(Invoice.total), 0)).where(
                    Invoice.organization_id == org_id, Invoice.status != "cancelled", Invoice.status != "draft", Invoice.issue_date >= month_start
                )
            )
            or 0
        )
        received = (
            s.scalar(
                select(func.coalesce(func.sum(PaymentRecord.amount), 0)).where(
                    PaymentRecord.organization_id == org_id, PaymentRecord.status == "confirmed", PaymentRecord.received_on >= month_start
                )
            )
            or 0
        )
        figures = {
            "outstanding": format(outstanding, "f"),
            "overdue": format(overdue, "f"),
            "invoiced_this_month": format(Decimal(invoiced), "f"),
            "received_this_month": format(Decimal(received), "f"),
            "open_invoices": len(open_rows),
            "month": month_start.isoformat()[:7],
        }
        reply = t(
            f"สรุปเดือนนี้: ออกใบแจ้งหนี้ {_money(Decimal(invoiced))} บาท รับชำระแล้ว {_money(Decimal(received))} บาท "
            f"ยอดลูกหนี้คงค้างทั้งหมด {_money(outstanding)} บาท เกินกำหนด {_money(overdue)} บาท",
            f"This month: invoiced {_money(Decimal(invoiced))} baht, received {_money(Decimal(received))} baht. "
            f"Outstanding receivables {_money(outstanding)} baht, of which {_money(overdue)} overdue.",
        )
        return {"agent": self.name, "reply": reply, "figures": figures, "link": "/qtc/invoices?status=open"}


# ---- CollectionAgent ---------------------------------------------------------------------------------
class ReminderText(BaseModel):
    text: str = Field(max_length=1200)


_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+\.\d{2}")


def reminder_text_ok(text: str, number: str, balance: Decimal) -> bool:
    """AI output validation: the reminder must name the invoice and its exact balance, and no other amount."""
    if number not in text or len(text) > 1200:
        return False
    amounts = [Decimal(a.replace(",", "")) for a in _AMOUNT.findall(text)]
    return bool(amounts) and all(a == balance for a in amounts)


class CollectionAgent:
    """Prepares payment-reminder drafts for overdue invoices. It never sends anything."""

    name = "collection"

    def __init__(self, ctx: QtcAgentContext):
        self.ctx = ctx

    def overdue(self) -> list[Invoice]:
        return list(
            self.ctx.session.scalars(
                select(Invoice)
                .where(Invoice.organization_id == self.ctx.org.id, Invoice.status.in_(OPEN_INVOICE), Invoice.due_date < date.today())
                .order_by(Invoice.due_date)
            )
        )

    def propose(self) -> dict[str, Any]:
        t = self.ctx.t
        rows = self.overdue()
        if not rows:
            return {"agent": self.name, "reply": t("ไม่มีใบแจ้งหนี้ที่เกินกำหนดค่ะ", "No invoice is overdue."), "items": []}
        customers = {c.id: c for c in self.ctx.session.scalars(select(Customer).where(Customer.id.in_({i.customer_id for i in rows})))}
        items = [
            {
                "invoice_id": str(i.id),
                "number": i.number,
                "customer": customers[i.customer_id].name,
                "balance": format(i.total - i.paid_amount, "f"),
                "due_date": i.due_date.isoformat() if i.due_date else None,
            }
            for i in rows
        ]
        reply = t(
            f"มีใบแจ้งหนี้เกินกำหนด {len(rows)} ใบ ต้องการให้เตรียมร่างข้อความติดตามเงินไหมคะ ระบบจะยังไม่ส่งข้อความใด ๆ จนกว่าจะอนุมัติ",
            f"{len(rows)} invoice(s) are overdue. Shall I prepare reminder drafts? Nothing is sent until approved.",
        )
        return {
            "agent": self.name,
            "reply": reply,
            "items": items,
            "confirm": {"action": "draft_reminders", "payload": {"invoice_ids": [x["invoice_id"] for x in items]}},
        }

    def _template(self, inv: Invoice, customer: Customer) -> str:
        bal = inv.total - inv.paid_amount
        due = inv.due_date.isoformat() if inv.due_date else "-"
        return self.ctx.t(
            f"เรียน {customer.legal_name or customer.name}\n\nขอเรียนแจ้งว่าใบแจ้งหนี้เลขที่ {inv.number} ครบกำหนดชำระเมื่อวันที่ {due} "
            f"ยอดคงค้าง {_money(bal)} บาท หากท่านชำระแล้ว กรุณาส่งหลักฐานการชำระเงินกลับมา ขออภัยหากชำระแล้วค่ะ\n\nขอบคุณค่ะ",
            f"Dear {customer.legal_name or customer.name},\n\nInvoice {inv.number} was due on {due}; the outstanding balance is "
            f"{_money(bal)} baht. If you have already paid, please send us the payment evidence.\n\nThank you.",
        )

    def draft(self, invoice_ids: list[uuid.UUID]) -> list[PaymentReminder]:
        s, org_id = self.ctx.session, self.ctx.org.id
        out = []
        for inv in self.overdue():
            if inv.id not in invoice_ids:
                continue
            customer = s.get(Customer, inv.customer_id)
            assert customer is not None
            contact = s.scalar(
                select(CustomerContact)
                .where(CustomerContact.customer_id == customer.id)
                .order_by(CustomerContact.is_primary.desc(), CustomerContact.created_at)
            )
            # Only a channel the contact agreed to; otherwise the reminder is for a person to deliver ("manual").
            channel = (
                "email"
                if contact and contact.consent_email and contact.email
                else ("line" if contact and contact.consent_line and contact.line_id else "manual")
            )
            bal = inv.total - inv.paid_amount
            text = self._template(inv, customer)
            ai = self.ctx.llm(
                self.name,
                "Write a short, polite payment reminder in the requested language. Use exactly the invoice number "
                "and balance given; mention no other amounts; no threats.",
                f"Language: {self.ctx.language}\nCustomer: {customer.name}\nInvoice: {inv.number}\nBalance: {_money(bal)} baht\n"
                f"Due date: {inv.due_date}",
                ReminderText,
            )
            used_ai = bool(ai and reminder_text_ok(ai.text, inv.number or "", bal))
            if used_ai and ai is not None:
                text = ai.text
            r = PaymentReminder(
                organization_id=org_id,
                invoice_id=inv.id,
                contact_id=contact.id if contact else None,
                channel=channel,
                status="draft",
                body=text,
                created_by=self.ctx.actor,
            )
            s.add(r)
            s.flush()
            audit.record(
                s, org_id, "reminder", r.id, "reminder.drafted", self.ctx.actor, {"invoice": inv.number, "channel": channel, "ai_text_used": used_ai}
            )
            out.append(r)
        return out


for _spec in (
    AgentSpec("sales_intake", "qtc", "Reads a sales inquiry: customer and requested services", None),
    AgentSpec("quotation", "qtc", "Prepares draft-quotation proposals priced only from the approved list", None),
    AgentSpec("finance", "qtc", "Answers receivables and approval questions from exact queries", None),
    AgentSpec("collection", "qtc", "Drafts payment reminders for overdue invoices; never sends", None),
):
    register(_spec)
