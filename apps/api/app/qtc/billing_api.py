"""/v1/qtc orders, invoices, payments and receivables (Phase C).

Rules
- An order can only be made from an ACCEPTED quotation, copies the accepted revision exactly, and the
  database allows one order per quotation (duplicate conversion returns 409 with the existing order).
- The billing schedule (deposit / installments / final) must add up exactly to the order total; amounts and
  their VAT are split deterministically (the last part takes the rounding remainder).
- Invoices are commercial invoices (ใบแจ้งหนี้). They are numbered when issued, never before. Tax invoices
  and receipts are NOT issued: that stays disabled (`QTC_TAX_DOCUMENTS_ENABLED`) until Thai legal and
  accounting requirements are verified.
- A payment is recorded as "pending confirmation" and changes nothing. Only a signed-in owner or admin
  confirming it applies the money to invoices. An uploaded transfer slip is evidence, never a confirmation.
  Confirmation is atomic: an allocation that would exceed an invoice's balance fails as a whole.
- Every state change locks the row and claims it with a conditional UPDATE (exactly one concurrent request
  wins); every change is audited in the same transaction.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile
from fastapi.responses import Response as RawResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.deps import Principal, get_principal, require_admin
from app.errors import AppError, forbidden, not_found
from app.models import Project
from app.qtc import audit, numbering
from app.qtc import documents as docs
from app.qtc import quotations as engine
from app.qtc.api import require_user
from app.qtc.models import (
    Customer,
    Invoice,
    InvoiceLine,
    PaymentAllocation,
    PaymentRecord,
    Quotation,
    QuotationRevision,
    SalesOrder,
    SalesOrderLine,
    SalesOrderMilestone,
)
from app.qtc.money import ZERO, q2
from app.qtc.schemas import DecimalStr, _decimal_field
from app.services.storage import get_storage

router = APIRouter(prefix="/v1/qtc", tags=["quote-to-cash"])
BILLING = ("deposit", "installment", "final")
OPEN_INVOICE = ("issued", "partially_paid")
EVIDENCE_TYPES = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_EVIDENCE = 10 * 1024 * 1024


def _claim(session: Session, model: Any, row: Any, allowed: set[str], action: str, *extra: Any) -> None:
    res = session.execute(
        update(model).where(model.id == row.id, model.status.in_(allowed), *extra).values(updated_at=utcnow())
        if hasattr(model, "updated_at")
        else update(model).where(model.id == row.id, model.status.in_(allowed), *extra).values(status=model.status)
    )
    if res.rowcount != 1:  # type: ignore[attr-defined]
        session.rollback()
        session.refresh(row)
        raise AppError(409, "INVALID_STATE", f"Cannot {action}: it is {row.status}", {"status": row.status})


def _get(session: Session, model: Any, org_id: uuid.UUID, row_id: uuid.UUID, what: str, *, lock: bool = False) -> Any:
    stmt = select(model).where(model.id == row_id, model.organization_id == org_id)
    row = session.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise not_found(what)
    return row


def _money(v: object, field: str) -> Decimal:
    return _decimal_field(v, field, places=2, minimum=Decimal("0"))


# ---- schedule ------------------------------------------------------------------------------------
class ScheduleItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["deposit", "installment", "final"]
    description: str = Field(min_length=1, max_length=300)
    due_date: date | None = None
    percent: Decimal | None = None
    amount: Decimal | None = None  # including VAT

    @field_validator("percent", mode="before")
    @classmethod
    def _pct(cls, v: object) -> Decimal | None:
        return None if v is None else _decimal_field(v, "percent", places=4, minimum=Decimal("0"), maximum=Decimal("100"))

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v: object) -> Decimal | None:
        return None if v is None else _money(v, "amount")


def split_schedule(total: Decimal, vat_total: Decimal, items: list[ScheduleItem]) -> list[tuple[ScheduleItem, Decimal, Decimal, Decimal]]:
    """(item, net, vat, total) per item; adds up exactly to the order (last item takes the remainder)."""
    if not items:
        raise AppError(422, "VALIDATION_ERROR", "The billing schedule needs at least one item")
    by_pct = [i.percent is not None for i in items]
    by_amt = [i.amount is not None for i in items]
    if not (all(by_pct) and not any(by_amt)) and not (all(by_amt) and not any(by_pct)):
        raise AppError(422, "VALIDATION_ERROR", "Give every schedule item either a percent or an amount (not a mix)")
    if all(by_pct):
        if sum((i.percent or ZERO) for i in items) != Decimal("100"):
            raise AppError(422, "SCHEDULE_MISMATCH", "Schedule percentages must add up to exactly 100")
        totals = [q2(total * (i.percent or ZERO) / Decimal("100")) for i in items[:-1]]
        totals.append(total - sum(totals, ZERO))
    else:
        totals = [i.amount or ZERO for i in items]
        if sum(totals, ZERO) != total:
            raise AppError(
                422,
                "SCHEDULE_MISMATCH",
                f"Schedule amounts must add up to the order total {total}",
                {"order_total": format(total, "f"), "schedule_total": format(sum(totals, ZERO), "f")},
            )
    vats = [q2(vat_total * t / total) if total else ZERO for t in totals[:-1]]
    vats.append(vat_total - sum(vats, ZERO))
    out = []
    for item, t, v in zip(items, totals, vats, strict=True):
        if t < 0 or v < 0 or v > t:
            raise AppError(422, "SCHEDULE_MISMATCH", "A schedule item comes out negative; check the amounts")
        out.append((item, t - v, v, t))
    return out


# ---- orders --------------------------------------------------------------------------------------
class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quotation_id: uuid.UUID
    project_id: uuid.UUID | None = None
    notes: str = Field(default="", max_length=4000)
    schedule: list[ScheduleItem] | None = Field(default=None, max_length=24)  # default: 100 % on delivery
    delivery_milestones: bool = True  # one delivery milestone per line of work


class MilestoneOut(BaseModel):
    id: uuid.UUID
    position: int
    kind: str
    description: str
    due_date: date | None
    amount_net: DecimalStr
    amount_vat: DecimalStr
    amount_total: DecimalStr
    status: str
    invoice_id: uuid.UUID | None = None


class OrderOut(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    quotation_id: uuid.UUID
    quotation_number: str
    quotation_revision: int
    customer: dict
    project_id: uuid.UUID | None
    vat_rate: DecimalStr
    prices_include_vat: bool
    subtotal: DecimalStr
    vat_total: DecimalStr
    grand_total: DecimalStr
    invoiced_total: DecimalStr
    paid_total: DecimalStr
    notes: str
    created_by: str
    created_at: datetime
    lines: list[dict] = Field(default_factory=list)
    milestones: list[MilestoneOut] = Field(default_factory=list)


def _order_out(session: Session, o: SalesOrder) -> OrderOut:
    customer = session.get(Customer, o.customer_id)
    q = session.get(Quotation, o.quotation_id)
    live = {i.milestone_id: i for i in session.scalars(select(Invoice).where(Invoice.order_id == o.id, Invoice.status != "cancelled"))}
    lines = session.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == o.id).order_by(SalesOrderLine.position)).all()
    ms = session.scalars(select(SalesOrderMilestone).where(SalesOrderMilestone.order_id == o.id).order_by(SalesOrderMilestone.position)).all()
    f = lambda d: format(d, "f")  # noqa: E731
    return OrderOut(
        id=o.id,
        number=o.number,
        status=o.status,
        quotation_id=o.quotation_id,
        quotation_number=q.number if q else "",
        quotation_revision=o.quotation_revision,
        customer={"id": customer.id, "code": customer.code, "name": customer.name} if customer else {},
        project_id=o.project_id,
        vat_rate=o.vat_rate,
        prices_include_vat=o.prices_include_vat,
        subtotal=o.subtotal,
        vat_total=o.vat_total,
        grand_total=o.grand_total,
        invoiced_total=sum((i.total for i in live.values() if i.status != "draft"), ZERO),
        paid_total=sum((i.paid_amount for i in live.values()), ZERO),
        notes=o.notes,
        created_by=o.created_by,
        created_at=o.created_at,
        lines=[
            {
                "position": x.position,
                "description": x.description,
                "unit": x.unit,
                "quantity": f(x.quantity),
                "unit_price": f(x.unit_price),
                "line_net": f(x.line_net),
                "line_vat": f(x.line_vat),
                "line_total": f(x.line_total),
            }
            for x in lines
        ],
        milestones=[
            MilestoneOut(
                id=m.id,
                position=m.position,
                kind=m.kind,
                description=m.description,
                due_date=m.due_date,
                amount_net=m.amount_net,
                amount_vat=m.amount_vat,
                amount_total=m.amount_total,
                status=m.status,
                invoice_id=live[m.id].id if m.id in live else None,
            )
            for m in ms
        ],
    )


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(body: OrderCreate, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    """Convert an accepted quotation into a sales order, in one transaction."""
    org_id = principal.organization.id
    q = _get(session, Quotation, org_id, body.quotation_id, "Quotation", lock=True)
    existing = session.scalar(select(SalesOrder).where(SalesOrder.quotation_id == q.id))
    if existing is not None:
        raise AppError(409, "ALREADY_CONVERTED", f"This quotation is already order {existing.number}", {"order_id": str(existing.id)})
    if q.status != "accepted" or q.accepted_revision is None:
        raise AppError(409, "NOT_ACCEPTED", "Only a quotation the customer accepted can become an order", {"status": engine.effective_status(q)})
    rev = session.scalar(select(QuotationRevision).where(QuotationRevision.quotation_id == q.id, QuotationRevision.revision == q.accepted_revision))
    if rev is None:
        raise AppError(409, "NOT_ACCEPTED", "The accepted revision is missing")
    if body.project_id is not None:
        _get(session, Project, org_id, body.project_id, "Project")
    snap = rev.snapshot
    totals = snap["totals"]
    total, vat_total, subtotal = Decimal(totals["total"]), Decimal(totals["vat"]), Decimal(totals["subtotal"])
    schedule = body.schedule or [ScheduleItem(kind="final", description="ชำระเต็มจำนวน", percent=Decimal("100"))]
    parts = split_schedule(total, vat_total, schedule)
    issue = date.today()
    order = SalesOrder(
        organization_id=org_id,
        number=f"SO-{issue.year}-{numbering.take(session, org_id, f'order:{issue.year}'):04d}",
        quotation_id=q.id,
        quotation_revision=q.accepted_revision,
        customer_id=q.customer_id,
        project_id=body.project_id,
        currency=snap["currency"],
        prices_include_vat=snap["prices_include_vat"],
        vat_rate=Decimal(snap["vat_rate"]),
        subtotal=subtotal,
        vat_total=vat_total,
        grand_total=total,
        accepted_snapshot=snap,
        notes=body.notes,
        created_by=principal.actor,
    )
    try:
        with session.begin_nested():
            session.add(order)
            session.flush()
    except IntegrityError:
        other = session.scalar(select(SalesOrder).where(SalesOrder.quotation_id == q.id))
        raise AppError(409, "ALREADY_CONVERTED", "This quotation is already an order", {"order_id": str(other.id) if other else None}) from None
    for line in snap["lines"]:
        session.add(
            SalesOrderLine(
                organization_id=org_id,
                order_id=order.id,
                position=line["position"],
                service_id=uuid.UUID(line["service_id"]) if line["service_id"] else None,
                description=line["description"],
                unit=line["unit"],
                quantity=Decimal(line["quantity"]),
                unit_price=Decimal(line["unit_price"]),
                line_net=Decimal(line["line_net"]),
                line_vat=Decimal(line["line_vat"]),
                line_total=Decimal(line["line_total"]),
            )
        )
    pos = 0
    for item, net, vat, gross in parts:
        pos += 1
        session.add(
            SalesOrderMilestone(
                organization_id=org_id,
                order_id=order.id,
                position=pos,
                kind=item.kind,
                description=item.description,
                due_date=item.due_date,
                amount_net=net,
                amount_vat=vat,
                amount_total=gross,
            )
        )
    if body.delivery_milestones:
        for line in snap["lines"]:
            pos += 1
            session.add(
                SalesOrderMilestone(
                    organization_id=org_id, order_id=order.id, position=pos, kind="delivery", description=f"ส่งมอบ: {line['description']}"[:300]
                )
            )
    audit.record(
        session,
        org_id,
        "order",
        order.id,
        "order.created",
        principal.actor,
        {
            "number": order.number,
            "quotation": q.number,
            "revision": q.accepted_revision,
            "total": total,
            "schedule": [{"kind": i.kind, "total": g} for i, _, _, g in parts],
        },
    )
    audit.record(session, org_id, "quotation", q.id, "quotation.converted", principal.actor, {"order": order.number})
    session.commit()
    return _order_out(session, order)


class OrderList(BaseModel):
    items: list[OrderOut]
    total: int
    limit: int
    offset: int


@router.get("/orders", response_model=OrderList)
def list_orders(
    status: str | None = Query(default=None, pattern="^(open|completed|cancelled)$"),
    customer_id: uuid.UUID | None = None,
    quotation_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    where: list[Any] = [SalesOrder.organization_id == principal.organization.id]
    if status:
        where.append(SalesOrder.status == status)
    if customer_id:
        where.append(SalesOrder.customer_id == customer_id)
    if quotation_id:
        where.append(SalesOrder.quotation_id == quotation_id)
    total = session.scalar(select(func.count()).select_from(SalesOrder).where(*where)) or 0
    rows = session.scalars(select(SalesOrder).where(*where).order_by(SalesOrder.created_at.desc()).limit(limit).offset(offset)).all()
    return OrderList(items=[_order_out(session, o) for o in rows], total=total, limit=limit, offset=offset)


@router.get("/orders/{order_id}", response_model=OrderOut)
def get_order(order_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return _order_out(session, _get(session, SalesOrder, principal.organization.id, order_id, "Order"))


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=2000)


@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_order(order_id: uuid.UUID, body: ReasonIn, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    o = _get(session, SalesOrder, org_id, order_id, "Order", lock=True)
    issued = session.scalar(select(func.count()).select_from(Invoice).where(Invoice.order_id == o.id, Invoice.status.in_(OPEN_INVOICE + ("paid",))))
    if issued:
        raise AppError(409, "HAS_INVOICES", "Cancel the order's issued invoices first (paid invoices need a credit note)")
    _claim(session, SalesOrder, o, {"open"}, "cancel the order")
    session.execute(
        update(Invoice)
        .where(Invoice.order_id == o.id, Invoice.status == "draft")
        .values(status="cancelled", cancelled_by=principal.actor, cancelled_reason="order cancelled")
    )
    session.execute(update(SalesOrderMilestone).where(SalesOrderMilestone.order_id == o.id).values(status="cancelled"))
    o.status, o.cancelled_reason = "cancelled", body.reason
    audit.record(session, org_id, "order", o.id, "order.cancelled", principal.actor, {"reason": body.reason})
    session.commit()
    return _order_out(session, o)


@router.post("/orders/{order_id}/milestones/{milestone_id}/done", response_model=OrderOut)
def delivery_done(order_id: uuid.UUID, milestone_id: uuid.UUID, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    o = _get(session, SalesOrder, org_id, order_id, "Order")
    m = session.scalar(select(SalesOrderMilestone).where(SalesOrderMilestone.id == milestone_id, SalesOrderMilestone.order_id == o.id))
    if m is None or m.kind != "delivery":
        raise not_found("Delivery milestone")
    if m.status != "done":
        m.status, m.done_at = "done", utcnow()
        audit.record(session, org_id, "order", o.id, "order.delivered", principal.actor, {"milestone": m.description})
        session.commit()
    return _order_out(session, o)


# ---- invoices ------------------------------------------------------------------------------------
class InvoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: uuid.UUID
    milestone_id: uuid.UUID
    notes: str = Field(default="", max_length=4000)


class InvoiceOut(BaseModel):
    id: uuid.UUID
    number: str | None
    document_type: str
    status: str  # includes the computed "overdue"
    order_id: uuid.UUID
    order_number: str
    milestone_id: uuid.UUID
    customer: dict
    issue_date: date | None
    due_date: date | None
    vat_rate: DecimalStr
    subtotal: DecimalStr
    vat_total: DecimalStr
    total: DecimalStr
    paid_amount: DecimalStr
    balance: DecimalStr
    pending_payments: DecimalStr  # recorded but not yet confirmed
    amount_in_words: str
    notes: str
    created_by: str
    issued_by: str | None
    cancelled_reason: str | None
    lines: list[dict] = Field(default_factory=list)
    allocations: list[dict] = Field(default_factory=list)


def effective_invoice_status(i: Invoice, today: date | None = None) -> str:
    if i.status in OPEN_INVOICE and i.due_date and i.due_date < (today or date.today()):
        return "overdue"
    return i.status


def _invoice_out(session: Session, i: Invoice) -> InvoiceOut:
    customer = session.get(Customer, i.customer_id)
    order = session.get(SalesOrder, i.order_id)
    lines = session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == i.id).order_by(InvoiceLine.position)).all()
    allocs = session.execute(
        select(PaymentAllocation, PaymentRecord)
        .join(PaymentRecord, PaymentRecord.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == i.id)
        .order_by(PaymentRecord.received_on)
    ).all()
    pending = sum((a.amount for a, p in allocs if p.status == "pending_confirmation"), ZERO)
    f = lambda d: format(d, "f")  # noqa: E731
    return InvoiceOut(
        id=i.id,
        number=i.number,
        document_type=i.document_type,
        status=effective_invoice_status(i),
        order_id=i.order_id,
        order_number=order.number if order else "",
        milestone_id=i.milestone_id,
        customer={"id": customer.id, "code": customer.code, "name": customer.name} if customer else {},
        issue_date=i.issue_date,
        due_date=i.due_date,
        vat_rate=i.vat_rate,
        subtotal=i.subtotal,
        vat_total=i.vat_total,
        total=i.total,
        paid_amount=i.paid_amount,
        balance=i.total - i.paid_amount,
        pending_payments=pending,
        amount_in_words=engine.thai_baht_text(i.total),
        notes=i.notes,
        created_by=i.created_by,
        issued_by=i.issued_by,
        cancelled_reason=i.cancelled_reason,
        lines=[
            {
                "position": x.position,
                "description": x.description,
                "unit": x.unit,
                "quantity": f(x.quantity),
                "unit_price": f(x.unit_price),
                "line_net": f(x.line_net),
                "line_vat": f(x.line_vat),
                "line_total": f(x.line_total),
            }
            for x in lines
        ],
        allocations=[
            {"payment_id": str(p.id), "payment_number": p.number, "amount": f(a.amount), "status": p.status, "received_on": p.received_on.isoformat()}
            for a, p in allocs
        ],
    )


@router.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(body: InvoiceCreate, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    """Draft a commercial invoice for one billing milestone of an open order."""
    org_id = principal.organization.id
    o = _get(session, SalesOrder, org_id, body.order_id, "Order", lock=True)
    if o.status != "open":
        raise AppError(409, "INVALID_STATE", f"The order is {o.status}")
    m = session.scalar(
        select(SalesOrderMilestone).where(SalesOrderMilestone.id == body.milestone_id, SalesOrderMilestone.order_id == o.id).with_for_update()
    )
    if m is None or m.kind not in BILLING:
        raise not_found("Billing milestone")
    if m.status != "pending":
        raise AppError(409, "ALREADY_INVOICED", "This milestone already has an invoice")
    inv = Invoice(
        organization_id=org_id,
        order_id=o.id,
        milestone_id=m.id,
        customer_id=o.customer_id,
        vat_rate=o.vat_rate,
        prices_include_vat=o.prices_include_vat,
        subtotal=m.amount_net,
        vat_total=m.amount_vat,
        total=m.amount_total,
        notes=body.notes,
        created_by=principal.actor,
    )
    try:
        with session.begin_nested():
            session.add(inv)
            session.flush()
    except IntegrityError:
        raise AppError(409, "ALREADY_INVOICED", "This milestone already has an invoice") from None
    whole = m.amount_total == o.grand_total
    if whole:  # one invoice for the whole order: the order's own lines
        for x in session.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == o.id).order_by(SalesOrderLine.position)):
            session.add(
                InvoiceLine(
                    organization_id=org_id,
                    invoice_id=inv.id,
                    position=x.position,
                    description=x.description,
                    unit=x.unit,
                    quantity=x.quantity,
                    unit_price=x.unit_price,
                    line_net=x.line_net,
                    line_vat=x.line_vat,
                    line_total=x.line_total,
                )
            )
    else:
        unit_price = m.amount_total if o.prices_include_vat else m.amount_net
        session.add(
            InvoiceLine(
                organization_id=org_id,
                invoice_id=inv.id,
                position=1,
                description=f"{m.description} ตามใบสั่งขาย {o.number}",
                unit="งวด",
                quantity=Decimal("1"),
                unit_price=unit_price,
                line_net=m.amount_net,
                line_vat=m.amount_vat,
                line_total=m.amount_total,
            )
        )
    m.status = "invoiced"
    audit.record(
        session, org_id, "invoice", inv.id, "invoice.drafted", principal.actor, {"order": o.number, "milestone": m.description, "total": inv.total}
    )
    session.commit()
    return _invoice_out(session, inv)


def _invoice_snapshot(session: Session, i: Invoice) -> dict[str, Any]:
    customer = session.get(Customer, i.customer_id)
    assert customer is not None
    lines = session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == i.id).order_by(InvoiceLine.position)).all()
    f = lambda d: format(d, "f")  # noqa: E731
    order = session.get(SalesOrder, i.order_id)
    return {
        "number": i.number or "(ร่าง)",
        "revision": 1,
        "title": f"ใบสั่งขาย {order.number}" if order else "",
        "issue_date": (i.issue_date or date.today()).isoformat(),
        "due_date": (i.due_date or date.today()).isoformat(),
        "currency": "THB",
        "prices_include_vat": i.prices_include_vat,
        "vat_rate": f(i.vat_rate),
        "notes": i.notes,
        "terms": "",
        "customer": {
            "id": str(customer.id),
            "code": customer.code,
            "name": customer.name,
            "legal_name": customer.legal_name,
            "tax_id": customer.tax_id,
            "branch": customer.branch,
            "address": customer.address,
        },
        "lines": [
            {
                "position": x.position,
                "service_id": None,
                "description": x.description,
                "unit": x.unit,
                "quantity": f(x.quantity),
                "unit_price": f(x.unit_price),
                "price_source": "price_list",
                "discount_percent": "0",
                "discount_amount": "0",
                "vat_exempt": False,
                "line_net": f(x.line_net),
                "line_vat": f(x.line_vat),
                "line_total": f(x.line_total),
            }
            for x in lines
        ],
        "totals": {"subtotal": f(i.subtotal), "discount": "0.00", "vat": f(i.vat_total), "total": f(i.total)},
    }


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceOut)
def issue_invoice(invoice_id: uuid.UUID, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    i = _get(session, Invoice, org_id, invoice_id, "Invoice", lock=True)
    if i.status != "draft":
        raise AppError(409, "INVALID_STATE", f"Cannot issue an invoice that is {i.status}", {"status": i.status})
    _claim(session, Invoice, i, {"draft"}, "issue the invoice")
    customer = session.get(Customer, i.customer_id)
    assert customer is not None
    today = date.today()
    i.number = f"INV-{today.year}-{numbering.take(session, org_id, f'invoice:{today.year}'):04d}"
    i.status, i.issue_date, i.due_date = "issued", today, today + timedelta(days=customer.payment_terms_days)
    i.issued_by, i.issued_at = principal.actor, utcnow()
    i.snapshot = _invoice_snapshot(session, i)
    audit.record(session, org_id, "invoice", i.id, "invoice.issued", principal.actor, {"number": i.number, "total": i.total, "due_date": i.due_date})
    session.commit()
    return _invoice_out(session, i)


@router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(invoice_id: uuid.UUID, body: ReasonIn, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    """Cancel an unpaid invoice (its number is kept, never reused). Paid amounts need a credit note (designed, not built)."""
    org_id = principal.organization.id
    i = _get(session, Invoice, org_id, invoice_id, "Invoice", lock=True)
    if i.paid_amount > 0:
        raise AppError(409, "HAS_PAYMENTS", "Payments were applied to this invoice; it needs a credit note, not a cancellation")
    pending = session.scalar(
        select(func.count())
        .select_from(PaymentAllocation)
        .join(PaymentRecord, PaymentRecord.id == PaymentAllocation.payment_id)
        .where(PaymentAllocation.invoice_id == i.id, PaymentRecord.status == "pending_confirmation")
    )
    if pending:
        raise AppError(409, "HAS_PENDING_PAYMENTS", "Confirm or reject the payments recorded against this invoice first")
    _claim(session, Invoice, i, {"draft", "issued"}, "cancel the invoice", Invoice.paid_amount == 0)
    i.status, i.cancelled_by, i.cancelled_reason = "cancelled", principal.actor, body.reason
    session.execute(update(SalesOrderMilestone).where(SalesOrderMilestone.id == i.milestone_id).values(status="pending"))
    audit.record(session, org_id, "invoice", i.id, "invoice.cancelled", principal.actor, {"number": i.number, "reason": body.reason})
    session.commit()
    return _invoice_out(session, i)


@router.post("/invoices/{invoice_id}/tax-invoice")
def issue_tax_invoice(invoice_id: uuid.UUID, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    _get(session, Invoice, principal.organization.id, invoice_id, "Invoice")
    if not get_settings().qtc_tax_documents_enabled:
        raise AppError(
            403,
            "TAX_DOCUMENTS_DISABLED",
            "Tax invoices and receipts are not issued by this system until Thai legal and accounting requirements are verified",
        )
    raise AppError(501, "NOT_IMPLEMENTED", "Tax invoice issuance is designed but not implemented")


class InvoiceList(BaseModel):
    items: list[InvoiceOut]
    total: int
    limit: int
    offset: int


@router.get("/invoices", response_model=InvoiceList)
def list_invoices(
    status: str | None = Query(default=None, pattern="^(draft|issued|partially_paid|paid|cancelled|overdue|open)$"),
    customer_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    today = date.today()
    where: list[Any] = [Invoice.organization_id == principal.organization.id]
    if status == "overdue":
        where += [Invoice.status.in_(OPEN_INVOICE), Invoice.due_date < today]
    elif status == "open":
        where.append(Invoice.status.in_(OPEN_INVOICE))
    elif status:
        where.append(Invoice.status == status)
    if customer_id:
        where.append(Invoice.customer_id == customer_id)
    if order_id:
        where.append(Invoice.order_id == order_id)
    total = session.scalar(select(func.count()).select_from(Invoice).where(*where)) or 0
    rows = session.scalars(select(Invoice).where(*where).order_by(Invoice.created_at.desc()).limit(limit).offset(offset)).all()
    return InvoiceList(items=[_invoice_out(session, i) for i in rows], total=total, limit=limit, offset=offset)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return _invoice_out(session, _get(session, Invoice, principal.organization.id, invoice_id, "Invoice"))


@router.get("/invoices/{invoice_id}/document")
def invoice_document(
    invoice_id: uuid.UUID,
    format: str = Query(default="pdf", pattern="^(pdf|docx)$"),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    org_id = principal.organization.id
    i = _get(session, Invoice, org_id, invoice_id, "Invoice")
    issued = i.status != "draft" and i.snapshot is not None
    if not issued and principal.user is None:
        raise AppError(403, "NOT_ISSUED", "Only issued invoices are available to integrations")
    snap = i.snapshot if issued and i.snapshot else _invoice_snapshot(session, i)
    st = engine.settings_for(session, org_id)
    seller = {
        "name": principal.organization.name,
        "legal_name": st.legal_name,
        "tax_id": st.tax_id,
        "branch": st.branch,
        "address": st.address,
        "phone": st.phone,
        "email": st.email,
    }
    draft = not issued or i.status == "cancelled"
    if format == "pdf":
        data, media = docs.render_pdf(snap, seller, draft=draft, labels=docs.INVOICE_LABELS), "application/pdf"
    else:
        data, media = (
            docs.render_docx(snap, seller, draft=draft, labels=docs.INVOICE_LABELS),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    audit.record(session, org_id, "invoice", i.id, "invoice.document_downloaded", principal.actor, {"format": format, "issued": issued})
    session.commit()
    name = f"{i.number or 'INVOICE'}{'' if not draft else '-DRAFT'}.{format}"
    return RawResponse(
        content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"}
    )


# ---- payments ------------------------------------------------------------------------------------
class AllocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_id: uuid.UUID
    amount: Decimal

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v: object) -> Decimal:
        d = _money(v, "amount")
        if d <= 0:
            raise ValueError("amount must be greater than zero")
        return d


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: uuid.UUID
    amount: Decimal
    received_on: date
    method: Literal["bank_transfer", "cash", "cheque", "card", "promptpay", "other"]
    reference: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=2000)
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=50)

    @field_validator("amount", mode="before")
    @classmethod
    def _amt(cls, v: object) -> Decimal:
        d = _money(v, "amount")
        if d <= 0:
            raise ValueError("amount must be greater than zero")
        return d


class PaymentOut(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    customer: dict
    amount: DecimalStr
    allocated: DecimalStr
    unallocated: DecimalStr  # kept as customer credit
    received_on: date
    method: str
    reference: str
    note: str
    has_evidence: bool
    evidence_name: str | None
    recorded_by: str
    confirmed_by: str | None
    confirmed_at: datetime | None
    rejected_reason: str | None
    created_at: datetime
    allocations: list[dict] = Field(default_factory=list)


def _payment_out(session: Session, p: PaymentRecord) -> PaymentOut:
    customer = session.get(Customer, p.customer_id)
    rows = session.execute(
        select(PaymentAllocation, Invoice).join(Invoice, Invoice.id == PaymentAllocation.invoice_id).where(PaymentAllocation.payment_id == p.id)
    ).all()
    allocated = sum((a.amount for a, _ in rows), ZERO)
    return PaymentOut(
        id=p.id,
        number=p.number,
        status=p.status,
        customer={"id": customer.id, "code": customer.code, "name": customer.name} if customer else {},
        amount=p.amount,
        allocated=allocated,
        unallocated=p.amount - allocated,
        received_on=p.received_on,
        method=p.method,
        reference=p.reference,
        note=p.note,
        has_evidence=bool(p.evidence_key),
        evidence_name=p.evidence_name,
        recorded_by=p.recorded_by,
        confirmed_by=p.confirmed_by,
        confirmed_at=p.confirmed_at,
        rejected_reason=p.rejected_reason,
        created_at=p.created_at,
        allocations=[{"invoice_id": str(i.id), "invoice_number": i.number, "amount": format(a.amount, "f")} for a, i in rows],
    )


@router.post("/payments", response_model=PaymentOut, status_code=201)
def record_payment(
    body: PaymentCreate,
    response: Response,
    principal: Principal = Depends(require_user),
    session: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    """Record money received. Nothing changes on any invoice until an owner or admin confirms it."""
    org_id = principal.organization.id
    if idempotency_key:
        existing = session.scalar(
            select(PaymentRecord).where(PaymentRecord.organization_id == org_id, PaymentRecord.idempotency_key == idempotency_key)
        )
        if existing is not None:
            response.status_code = 200
            return _payment_out(session, existing)
    customer = _get(session, Customer, org_id, body.customer_id, "Customer")
    if body.received_on > date.today():
        raise AppError(422, "VALIDATION_ERROR", "received_on is in the future")
    if len({a.invoice_id for a in body.allocations}) != len(body.allocations):
        raise AppError(422, "VALIDATION_ERROR", "Each invoice may appear only once")
    if sum((a.amount for a in body.allocations), ZERO) > body.amount:
        raise AppError(422, "OVERALLOCATED", "Allocations add up to more than the payment")
    for a in body.allocations:
        inv = _get(session, Invoice, org_id, a.invoice_id, "Invoice")
        if inv.customer_id != customer.id:
            raise AppError(422, "VALIDATION_ERROR", f"Invoice {inv.number} belongs to another customer")
        if inv.status not in OPEN_INVOICE:
            raise AppError(422, "INVOICE_NOT_OPEN", f"Invoice {inv.number or '(draft)'} is {inv.status}")
        if a.amount > inv.total - inv.paid_amount:
            raise AppError(
                422,
                "OVERPAYMENT",
                f"{a.amount} is more than the balance of {inv.number} ({inv.total - inv.paid_amount}); "
                "record the difference as unallocated credit instead",
            )
    today = date.today()
    p = PaymentRecord(
        organization_id=org_id,
        number=f"PAY-{today.year}-{numbering.take(session, org_id, f'payment:{today.year}'):04d}",
        customer_id=customer.id,
        amount=body.amount,
        received_on=body.received_on,
        method=body.method,
        reference=body.reference.strip(),
        note=body.note,
        recorded_by=principal.actor,
        idempotency_key=idempotency_key or None,
    )
    try:
        with session.begin_nested():
            session.add(p)
            session.flush()
    except IntegrityError:
        if idempotency_key:
            existing = session.scalar(
                select(PaymentRecord).where(PaymentRecord.organization_id == org_id, PaymentRecord.idempotency_key == idempotency_key)
            )
            if existing is not None:
                response.status_code = 200
                return _payment_out(session, existing)
        raise AppError(409, "DUPLICATE_PAYMENT", f"A payment with reference {body.reference!r} is already recorded") from None
    for a in body.allocations:
        session.add(PaymentAllocation(organization_id=org_id, payment_id=p.id, invoice_id=a.invoice_id, amount=a.amount))
    audit.record(
        session,
        org_id,
        "payment",
        p.id,
        "payment.recorded",
        principal.actor,
        {
            "number": p.number,
            "amount": p.amount,
            "method": p.method,
            "reference": p.reference,
            "allocations": [{"invoice": str(a.invoice_id), "amount": a.amount} for a in body.allocations],
        },
    )
    session.commit()
    return _payment_out(session, p)


@router.post("/payments/{payment_id}/confirm", response_model=PaymentOut)
def confirm_payment(payment_id: uuid.UUID, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    """An owner or admin checked the money arrived. Applies the allocations atomically."""
    if principal.user is None:
        raise forbidden("Payments are confirmed by a signed-in owner or admin")
    org_id = principal.organization.id
    p = _get(session, PaymentRecord, org_id, payment_id, "Payment", lock=True)
    if p.status != "pending_confirmation":
        raise AppError(409, "INVALID_STATE", f"The payment is {p.status}", {"status": p.status})
    _claim(session, PaymentRecord, p, {"pending_confirmation"}, "confirm the payment")
    allocs = session.scalars(select(PaymentAllocation).where(PaymentAllocation.payment_id == p.id)).all()
    for a in allocs:
        res = session.execute(
            update(Invoice)
            .where(
                Invoice.id == a.invoice_id,
                Invoice.organization_id == org_id,
                Invoice.status.in_(OPEN_INVOICE),
                Invoice.paid_amount + a.amount <= Invoice.total,
            )
            .values(paid_amount=Invoice.paid_amount + a.amount)
        )
        if res.rowcount != 1:  # type: ignore[attr-defined]
            session.rollback()
            inv = session.get(Invoice, a.invoice_id)
            raise AppError(
                409,
                "ALLOCATION_EXCEEDS_BALANCE",
                f"Invoice {inv.number if inv else ''} no longer has a balance of {a.amount}; edit the allocation before confirming",
            )
    session.flush()
    orders: set[uuid.UUID] = set()
    for a in allocs:
        inv = session.get(Invoice, a.invoice_id)
        assert inv is not None
        session.refresh(inv)
        inv.status = "paid" if inv.paid_amount == inv.total else "partially_paid"
        orders.add(inv.order_id)
    p.status, p.confirmed_by, p.confirmed_at = "confirmed", principal.actor, utcnow()
    session.flush()  # the session does not autoflush: write the invoice statuses before checking the orders
    for order_id in orders:  # an order is completed when every billing milestone is invoiced and paid
        ms = session.scalars(select(SalesOrderMilestone).where(SalesOrderMilestone.order_id == order_id, SalesOrderMilestone.kind.in_(BILLING))).all()
        unpaid = session.scalar(
            select(func.count()).select_from(Invoice).where(Invoice.order_id == order_id, Invoice.status.in_(("draft",) + OPEN_INVOICE))
        )
        if ms and all(m.status == "invoiced" for m in ms) and not unpaid:
            session.execute(update(SalesOrder).where(SalesOrder.id == order_id, SalesOrder.status == "open").values(status="completed"))
    audit.record(
        session,
        org_id,
        "payment",
        p.id,
        "payment.confirmed",
        principal.actor,
        {"number": p.number, "amount": p.amount, "allocations": [{"invoice": str(a.invoice_id), "amount": a.amount} for a in allocs]},
    )
    session.commit()
    return _payment_out(session, p)


@router.post("/payments/{payment_id}/reject", response_model=PaymentOut)
def reject_payment(payment_id: uuid.UUID, body: ReasonIn, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    p = _get(session, PaymentRecord, org_id, payment_id, "Payment", lock=True)
    if p.status != "pending_confirmation":
        raise AppError(409, "INVALID_STATE", f"The payment is {p.status}", {"status": p.status})
    _claim(session, PaymentRecord, p, {"pending_confirmation"}, "reject the payment")
    p.status, p.rejected_reason = "rejected", body.reason
    audit.record(session, org_id, "payment", p.id, "payment.rejected", principal.actor, {"number": p.number, "reason": body.reason})
    session.commit()
    return _payment_out(session, p)


@router.post("/payments/{payment_id}/evidence", response_model=PaymentOut)
async def attach_evidence(
    payment_id: uuid.UUID, file: UploadFile = File(...), principal: Principal = Depends(require_user), session: Session = Depends(get_db)
):
    """Attach a transfer slip or receipt. Evidence never confirms the payment by itself."""
    org_id = principal.organization.id
    p = _get(session, PaymentRecord, org_id, payment_id, "Payment")
    ext = EVIDENCE_TYPES.get((file.content_type or "").split(";")[0])
    if ext is None:
        raise AppError(415, "UNSUPPORTED_FILE", "Evidence must be a PDF, JPEG, PNG or WebP file")
    data = await file.read(MAX_EVIDENCE + 1)
    if len(data) > MAX_EVIDENCE:
        raise AppError(413, "FILE_TOO_LARGE", "Evidence files are at most 10 MB")
    key = f"org/{org_id}/qtc/payments/{p.id}/{uuid.uuid4().hex}.{ext}"
    get_storage().put_bytes(key, data, file.content_type or "application/octet-stream")
    p.evidence_key, p.evidence_name, p.evidence_type = key, (file.filename or f"evidence.{ext}")[:200], file.content_type
    audit.record(
        session,
        org_id,
        "payment",
        p.id,
        "payment.evidence_attached",
        principal.actor,
        {"file": p.evidence_name, "bytes": len(data), "note": "evidence only; the payment still needs confirmation"},
    )
    session.commit()
    return _payment_out(session, p)


@router.get("/payments/{payment_id}/evidence")
def get_evidence(payment_id: uuid.UUID, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    p = _get(session, PaymentRecord, principal.organization.id, payment_id, "Payment")
    if not p.evidence_key:
        raise not_found("Evidence")
    return RawResponse(
        content=get_storage().read_bytes(p.evidence_key),
        media_type=p.evidence_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{p.evidence_name}"', "Cache-Control": "private, no-store"},
    )


class PaymentList(BaseModel):
    items: list[PaymentOut]
    total: int
    limit: int
    offset: int


@router.get("/payments", response_model=PaymentList)
def list_payments(
    status: str | None = Query(default=None, pattern="^(pending_confirmation|confirmed|rejected)$"),
    customer_id: uuid.UUID | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    where: list[Any] = [PaymentRecord.organization_id == principal.organization.id]
    if status:
        where.append(PaymentRecord.status == status)
    if customer_id:
        where.append(PaymentRecord.customer_id == customer_id)
    total = session.scalar(select(func.count()).select_from(PaymentRecord).where(*where)) or 0
    rows = session.scalars(select(PaymentRecord).where(*where).order_by(PaymentRecord.created_at.desc()).limit(limit).offset(offset)).all()
    return PaymentList(items=[_payment_out(session, p) for p in rows], total=total, limit=limit, offset=offset)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return _payment_out(session, _get(session, PaymentRecord, principal.organization.id, payment_id, "Payment"))


# ---- receivables -----------------------------------------------------------------------------------
@router.get("/receivables")
def receivables(principal: Principal = Depends(get_principal), session: Session = Depends(get_db)) -> dict[str, Any]:
    """Outstanding balances per customer (issued and partly paid invoices), overdue part, and unapplied credit."""
    org_id = principal.organization.id
    today = date.today()
    invoices = session.scalars(select(Invoice).where(Invoice.organization_id == org_id, Invoice.status.in_(OPEN_INVOICE))).all()
    per: dict[uuid.UUID, dict[str, Any]] = {}
    for i in invoices:
        row = per.setdefault(i.customer_id, {"outstanding": ZERO, "overdue": ZERO, "invoices": 0, "oldest_due": None})
        bal = i.total - i.paid_amount
        row["outstanding"] += bal
        row["invoices"] += 1
        if i.due_date and i.due_date < today:
            row["overdue"] += bal
        if i.due_date and (row["oldest_due"] is None or i.due_date < row["oldest_due"]):
            row["oldest_due"] = i.due_date
    credit: dict[uuid.UUID, Decimal] = {}
    for p in session.scalars(select(PaymentRecord).where(PaymentRecord.organization_id == org_id, PaymentRecord.status == "confirmed")):
        allocated = session.scalar(select(func.coalesce(func.sum(PaymentAllocation.amount), 0)).where(PaymentAllocation.payment_id == p.id)) or 0
        if p.amount - Decimal(allocated) > 0:
            credit[p.customer_id] = credit.get(p.customer_id, ZERO) + p.amount - Decimal(allocated)
    customers = {c.id: c for c in session.scalars(select(Customer).where(Customer.id.in_(set(per) | set(credit))))}
    items = []
    for cid in sorted(set(per) | set(credit), key=lambda x: -(per.get(x, {}).get("outstanding", ZERO))):
        row = per.get(cid, {"outstanding": ZERO, "overdue": ZERO, "invoices": 0, "oldest_due": None})
        c = customers[cid]
        items.append(
            {
                "customer": {"id": str(c.id), "code": c.code, "name": c.name},
                "outstanding": format(row["outstanding"], "f"),
                "overdue": format(row["overdue"], "f"),
                "invoices": row["invoices"],
                "oldest_due": row["oldest_due"].isoformat() if row["oldest_due"] else None,
                "unapplied_credit": format(credit.get(cid, ZERO), "f"),
            }
        )
    return {
        "items": items,
        "total_outstanding": format(sum((Decimal(x["outstanding"]) for x in items), ZERO), "f"),
        "total_overdue": format(sum((Decimal(x["overdue"]) for x in items), ZERO), "f"),
        "as_of": today.isoformat(),
    }
