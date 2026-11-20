"""Quote-to-Cash dashboard figures: exact sums from the database (no AI, no estimates).

- Pipeline: open quotations (draft, waiting for approval, approved or sent and not expired).
- Awaiting approval: quotations submitted and not yet decided.
- Accepted this month: quotations whose acceptance was recorded this month.
- Outstanding: unpaid balances of issued and partly paid invoices.
- Overdue: the part of those balances whose due date has passed.
- Cash received this month: confirmed payments only (a recorded but unconfirmed transfer does not count).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import Principal, get_principal
from app.qtc.models import Invoice, PaymentRecord, Quotation
from app.qtc.models import QuotationStatus as St

router = APIRouter(prefix="/v1/qtc", tags=["quote-to-cash"])
OPEN_INVOICE = ("issued", "partially_paid")


def _fig(count: int, amount: Any) -> dict[str, Any]:
    return {"count": int(count or 0), "amount": f"{Decimal(amount or 0):.2f}"}


def _quotes(session: Session, org_id: uuid.UUID, *where: Any) -> dict[str, Any]:
    count, amount = session.execute(
        select(func.count(Quotation.id), func.coalesce(func.sum(Quotation.grand_total), 0)).where(Quotation.organization_id == org_id, *where)
    ).one()
    return _fig(count, amount)


def summary(session: Session, org_id: uuid.UUID, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    month_start = today.replace(day=1)
    month_start_at = datetime(month_start.year, month_start.month, 1, tzinfo=UTC)
    pipeline = _quotes(
        session,
        org_id,
        or_(
            Quotation.status.in_((St.DRAFT, St.PENDING_APPROVAL)),
            and_(Quotation.status.in_((St.APPROVED, St.SENT)), Quotation.valid_until >= today),
        ),
    )
    awaiting = _quotes(session, org_id, Quotation.status == St.PENDING_APPROVAL)
    accepted = _quotes(session, org_id, Quotation.status == St.ACCEPTED, Quotation.accepted_at >= month_start_at)

    balance = Invoice.total - Invoice.paid_amount
    open_where = (Invoice.organization_id == org_id, Invoice.status.in_(OPEN_INVOICE))
    o_count, o_amount = session.execute(select(func.count(Invoice.id), func.coalesce(func.sum(balance), 0)).where(*open_where)).one()
    d_count, d_amount = session.execute(
        select(func.count(Invoice.id), func.coalesce(func.sum(balance), 0)).where(*open_where, Invoice.due_date < today)
    ).one()
    c_count, c_amount = session.execute(
        select(func.count(PaymentRecord.id), func.coalesce(func.sum(PaymentRecord.amount), 0)).where(
            PaymentRecord.organization_id == org_id, PaymentRecord.status == "confirmed", PaymentRecord.received_on >= month_start
        )
    ).one()
    return {
        "as_of": today.isoformat(),
        "month": month_start.isoformat()[:7],
        "pipeline": pipeline,
        "awaiting_approval": awaiting,
        "accepted_this_month": accepted,
        "outstanding": _fig(o_count, o_amount),
        "overdue": _fig(d_count, d_amount),
        "cash_received": _fig(c_count, c_amount),
        "counts": {"open_quotations": pipeline["count"], "open_invoices": int(o_count or 0)},
    }


@router.get("/dashboard")
def dashboard(principal: Principal = Depends(get_principal), session: Session = Depends(get_db)) -> dict[str, Any]:
    return summary(session, principal.organization.id)
