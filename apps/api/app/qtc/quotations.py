"""Quotation engine: pricing from approved price lists, deterministic totals, revisions, state changes.

Pricing rules
- A line for a catalogue service takes its unit price from the quotation's approved price list
  (`price_source="price_list"`).
- A person may type a different price; it is kept and marked `manual` (and audited).
- Anything without a price stays `missing` (unit_price NULL). A quotation with a missing price cannot be
  submitted. Neither this module nor any AI agent invents a commercial price.

States: draft → pending_approval → approved → sent → accepted (or declined), cancelled from any state
before acceptance. `revise` starts a new draft revision from a submitted one. An approved or sent
quotation past `valid_until` is reported as "expired" and cannot be accepted.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError, not_found
from app.qtc import numbering
from app.qtc.models import (
    Customer,
    PriceList,
    PriceListItem,
    QtcSettings,
    Quotation,
    QuotationLine,
    QuotationStatus,
    ServiceItem,
)
from app.qtc.money import DocumentTotals, LineAmounts, document_totals, line_amounts, q2, q_qty

S = QuotationStatus
EDITABLE = {S.DRAFT}
OPEN_FOR_ACCEPTANCE = {S.APPROVED, S.SENT}


def invalid_state(q: Quotation, action: str) -> AppError:
    return AppError(409, "INVALID_STATE", f"Cannot {action} a quotation that is {effective_status(q)}",
                    {"status": effective_status(q)})


def effective_status(q: Quotation, today: date | None = None) -> str:
    today = today or date.today()
    if q.status in OPEN_FOR_ACCEPTANCE and q.valid_until < today:
        return "expired"
    return q.status


def settings_for(session: Session, org_id: uuid.UUID) -> QtcSettings:
    s = session.get(QtcSettings, org_id)
    return s if s is not None else QtcSettings(organization_id=org_id)


def default_price_list(session: Session, org_id: uuid.UUID) -> PriceList:
    pl = session.scalar(select(PriceList).where(PriceList.organization_id == org_id, PriceList.is_default.is_(True),
                                                PriceList.status == "active"))
    if pl is None:
        raise AppError(422, "NO_APPROVED_PRICE_LIST", "Approve a price list before creating quotations")
    return pl


def approved_price_list(session: Session, org_id: uuid.UUID, price_list_id: uuid.UUID | None) -> PriceList:
    if price_list_id is None:
        return default_price_list(session, org_id)
    pl = session.scalar(select(PriceList).where(PriceList.id == price_list_id, PriceList.organization_id == org_id))
    if pl is None:
        raise not_found("Price list")
    if pl.status != "active":
        raise AppError(422, "PRICE_LIST_NOT_APPROVED", "Only an approved price list can price a quotation")
    return pl


# ---- brief matching ----------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def match_brief(session: Session, org_id: uuid.UUID, brief: str) -> list[tuple[ServiceItem, str]]:
    """Services whose keywords, name or code appear in the brief (deterministic; picks services, never prices)."""
    text = _norm(brief)
    if not text:
        return []
    found: list[tuple[ServiceItem, str]] = []
    services = session.scalars(select(ServiceItem).where(ServiceItem.organization_id == org_id, ServiceItem.status == "active")
                               .order_by(ServiceItem.code)).all()
    for svc in services:
        candidates = [*svc.keywords, svc.name, svc.name_en, svc.code]
        hit = next((c for c in candidates if c and len(c.strip()) >= 2 and _norm(c) in text), None)
        if hit:
            found.append((svc, hit))
    return found


# ---- pricing and totals --------------------------------------------------------------------
def price_line(session: Session, org_id: uuid.UUID, pl: PriceList, raw: dict[str, Any], position: int) -> QuotationLine:
    service: ServiceItem | None = None
    if raw.get("service_id"):
        service = session.scalar(select(ServiceItem).where(ServiceItem.id == raw["service_id"], ServiceItem.organization_id == org_id))
        if service is None:
            raise AppError(422, "VALIDATION_ERROR", f"Line {position}: unknown service")
    description = (raw.get("description") or (service.name if service else "")).strip()
    if not description:
        raise AppError(422, "VALIDATION_ERROR", f"Line {position}: a description is required for a line without a service")
    typed: Decimal | None = raw.get("unit_price")
    listed: Decimal | None = None
    if service is not None:
        listed = session.scalar(select(PriceListItem.unit_price).where(PriceListItem.price_list_id == pl.id,
                                                                       PriceListItem.service_id == service.id))
    if typed is not None and (listed is None or typed != listed):
        unit_price, source = typed, "manual"
    elif listed is not None:
        unit_price, source = listed, "price_list"
    else:
        unit_price, source = None, "missing"
    return QuotationLine(
        organization_id=org_id, position=position, service_id=service.id if service else None, description=description,
        unit=(raw.get("unit") or (service.unit if service else "งาน")).strip() or "งาน",
        quantity=q_qty(raw.get("quantity") or Decimal("1")), unit_price=unit_price, price_source=source,
        discount_percent=raw.get("discount_percent") or Decimal("0"), discount_amount=q2(raw.get("discount_amount") or Decimal("0")),
        vat_exempt=bool(raw.get("vat_exempt", service.vat_exempt if service else False)),
    )


def compute(lines: list[QuotationLine], *, vat_rate: Decimal, prices_include_vat: bool) -> DocumentTotals:
    """Fill each line's amounts (zero while its price is missing) and return the document totals."""
    priced: list[LineAmounts] = []
    for line in lines:
        if line.unit_price is None:
            line.line_net = line.line_vat = line.line_total = Decimal("0.00")
            continue
        a = line_amounts(line.quantity, line.unit_price, discount_percent=line.discount_percent, discount_amount=line.discount_amount,
                         vat_rate=vat_rate, prices_include_vat=prices_include_vat, vat_exempt=line.vat_exempt)
        line.line_net, line.line_vat, line.line_total = a.net, a.vat, a.total
        priced.append(a)
    return document_totals(priced)


def apply_totals(q: Quotation, totals: DocumentTotals) -> None:
    q.subtotal, q.discount_total, q.vat_total, q.grand_total = totals.subtotal, totals.discount, totals.vat, totals.total


def lines_of(session: Session, q: Quotation) -> list[QuotationLine]:
    return list(session.scalars(select(QuotationLine).where(QuotationLine.quotation_id == q.id).order_by(QuotationLine.position)))


def replace_lines(session: Session, q: Quotation, pl: PriceList, raw_lines: list[dict[str, Any]]) -> list[QuotationLine]:
    for old in lines_of(session, q):
        session.delete(old)
    session.flush()
    lines = [price_line(session, q.organization_id, pl, raw, i + 1) for i, raw in enumerate(raw_lines)]
    apply_totals(q, compute(lines, vat_rate=q.vat_rate, prices_include_vat=q.prices_include_vat))
    for line in lines:
        line.quotation_id = q.id
        session.add(line)
    session.flush()
    return lines


def new_number(session: Session, org_id: uuid.UUID, issue: date) -> str:
    return f"QT-{issue.year}-{numbering.take(session, org_id, f'quotation:{issue.year}'):04d}"


def default_valid_until(session: Session, org_id: uuid.UUID, issue: date) -> date:
    return issue + timedelta(days=settings_for(session, org_id).default_valid_days or 30)


def snapshot(q: Quotation, lines: list[QuotationLine], customer: Customer) -> dict[str, Any]:
    """Everything printed on the document, frozen at submission (amounts as exact strings)."""
    s = lambda d: None if d is None else format(d, "f")  # noqa: E731
    return {
        "number": q.number, "revision": q.revision, "title": q.title, "issue_date": q.issue_date.isoformat(),
        "valid_until": q.valid_until.isoformat(), "currency": q.currency, "prices_include_vat": q.prices_include_vat,
        "vat_rate": s(q.vat_rate), "price_list_id": str(q.price_list_id), "notes": q.notes, "terms": q.terms,
        "customer": {"id": str(customer.id), "code": customer.code, "name": customer.name, "legal_name": customer.legal_name,
                     "tax_id": customer.tax_id, "branch": customer.branch, "address": customer.address},
        "lines": [{"position": x.position, "service_id": str(x.service_id) if x.service_id else None, "description": x.description,
                   "unit": x.unit, "quantity": s(x.quantity), "unit_price": s(x.unit_price), "price_source": x.price_source,
                   "discount_percent": s(x.discount_percent), "discount_amount": s(x.discount_amount), "vat_exempt": x.vat_exempt,
                   "line_net": s(x.line_net), "line_vat": s(x.line_vat), "line_total": s(x.line_total)} for x in lines],
        "totals": {"subtotal": s(q.subtotal), "discount": s(q.discount_total), "vat": s(q.vat_total), "total": s(q.grand_total)},
    }


# ---- Thai amount in words (printed under the total on Thai commercial documents) -------------
_DIGITS = ["", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า"]
_PLACES = ["", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน"]


def _thai_int(n: int) -> str:
    if n == 0:
        return ""
    if n >= 1_000_000:
        rest = n % 1_000_000
        return _thai_int(n // 1_000_000) + "ล้าน" + ("เอ็ด" if rest == 1 else _thai_int(rest))
    out = []
    digits = str(n)
    length = len(digits)
    for i, ch in enumerate(digits):
        d = int(ch)
        place = length - i - 1
        if d == 0:
            continue
        if place == 0 and d == 1 and length > 1:
            out.append("เอ็ด")
        elif place == 1 and d == 2:
            out.append("ยี่สิบ")
        elif place == 1 and d == 1:
            out.append("สิบ")
        else:
            out.append(_DIGITS[d] + _PLACES[place])
    return "".join(out)


def thai_baht_text(amount: Decimal) -> str:
    """Decimal('1250.50') -> 'หนึ่งพันสองร้อยห้าสิบบาทห้าสิบสตางค์'."""
    amount = q2(amount)
    if amount < 0:
        raise ValueError("negative amount")
    baht = int(amount)
    satang = int((amount - baht) * 100)
    text = (_thai_int(baht) or "ศูนย์") + "บาท"
    return text + (_thai_int(satang) + "สตางค์" if satang else "ถ้วน")
