"""Food & drink orders (module "orders").

Rules:
- Prices come only from the café's menu rows, read inside the same transaction that writes the order. A client (the
  seat screen, staff page or Mira) sends item IDs and quantities, never a price or a total.
- Each line copies the item's name and price, so editing the menu later never changes an order already placed.
- The order, its lines and the audit row are written in one transaction. An Idempotency-Key makes a retried submit
  return the first order instead of ordering twice.
- Payment is "at the counter": staff mark an order paid. No payment provider is connected, so nothing here charges
  anyone or reports a payment that did not happen.
"""
from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cafe import flags, service
from app.cafe.api import admin, staff
from app.cafe.models import CafeMenuCategory, CafeMenuItem, CafeOrder, CafeOrderLine, CafeSeat
from app.db import get_db, utcnow
from app.deps import Principal
from app.errors import AppError, conflict, not_found

router = APIRouter(prefix="/v1/cafe", tags=["smart-cafe-orders"], dependencies=[Depends(flags.module_dep("orders"))])

TWO = Decimal("0.01")
MAX_LINES = 30
NEXT_STATUS: dict[str, set[str]] = {"placed": {"preparing", "cancelled"}, "preparing": {"delivered", "cancelled"}}


def money(value: Decimal) -> str:
    return format(value.quantize(TWO, ROUND_HALF_UP), "f")


class OrderError(AppError):
    def __init__(self, code: str, message: str, status: int = 409, details: dict[str, Any] | None = None):
        super().__init__(status, code, message, details)


# ---- menu ----------------------------------------------------------------------------------------------------
def _item_out(i: CafeMenuItem) -> dict[str, Any]:
    return {"id": str(i.id), "category_id": str(i.category_id), "name": i.name, "description": i.description, "aliases": i.aliases or [],
            "price": money(i.price), "list_price": money(i.list_price) if i.list_price is not None else None, "is_new": i.is_new,
            "available": i.available, "sort": i.sort}


def menu(session: Session, org_id: uuid.UUID, *, include_hidden: bool = False) -> dict[str, Any]:
    cats = session.scalars(select(CafeMenuCategory).where(CafeMenuCategory.organization_id == org_id)
                           .order_by(CafeMenuCategory.sort, CafeMenuCategory.name)).all()
    items = session.scalars(select(CafeMenuItem).where(CafeMenuItem.organization_id == org_id)
                            .order_by(CafeMenuItem.sort, CafeMenuItem.name)).all()
    if not include_hidden:
        cats = [c for c in cats if c.active]
        active = {c.id for c in cats}
        items = [i for i in items if i.category_id in active]
    return {"categories": [{"id": str(c.id), "name": c.name, "sort": c.sort, "active": c.active} for c in cats],
            "items": [_item_out(i) for i in items]}


@router.get("/menu")
def get_menu(all: bool = False, principal: Principal = Depends(staff), session: Session = Depends(get_db)):  # noqa: A002
    return menu(session, principal.organization.id, include_hidden=all)


class CategoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    sort: int = Field(default=0, ge=0, le=10000)
    active: bool = True


@router.post("/menu/categories", status_code=201)
def create_category(body: CategoryIn, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    cat = CafeMenuCategory(organization_id=principal.organization.id, name=body.name.strip(), sort=body.sort, active=body.active)
    session.add(cat)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict(f"Category {body.name} already exists") from exc
    service.audit(session, cat.organization_id, "menu_category", cat.id, "menu_category.created", principal.actor, {"name": cat.name})
    session.commit()
    return {"id": str(cat.id), "name": cat.name, "sort": cat.sort, "active": cat.active}


class CategoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort: int | None = Field(default=None, ge=0, le=10000)
    active: bool | None = None


@router.patch("/menu/categories/{category_id}")
def patch_category(category_id: uuid.UUID, body: CategoryPatch, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    cat = session.get(CafeMenuCategory, category_id)
    if cat is None or cat.organization_id != principal.organization.id:
        raise not_found("Category")
    patch = body.model_dump(exclude_none=True)
    for k, v in patch.items():
        setattr(cat, k, v)
    service.audit(session, cat.organization_id, "menu_category", cat.id, "menu_category.updated", principal.actor, patch)
    session.commit()
    return {"id": str(cat.id), "name": cat.name, "sort": cat.sort, "active": cat.active}


def _price(value: Any, field: str) -> Decimal:
    if isinstance(value, bool | float):
        raise ValueError(f"{field}: send prices as text such as \"45.00\", not as a floating-point number")
    try:
        d = Decimal(str(value).strip())
    except Exception as exc:  # noqa: BLE001 - any parse failure is a validation error
        raise ValueError(f"{field}: {value!r} is not a price") from exc
    if not d.is_finite() or d < 0 or d > Decimal("100000") or d != d.quantize(TWO):
        raise ValueError(f"{field}: must be between 0 and 100000 with at most 2 decimals")
    return d


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    price: str | int
    list_price: str | int | None = None
    is_new: bool = False
    available: bool = True
    sort: int = Field(default=0, ge=0, le=10000)

    @field_validator("price", mode="before")
    @classmethod
    def _p(cls, v: Any) -> Any:
        return format(_price(v, "price"), "f")

    @field_validator("list_price", mode="before")
    @classmethod
    def _lp(cls, v: Any) -> Any:
        return None if v in (None, "") else format(_price(v, "list_price"), "f")


def _category(session: Session, org_id: uuid.UUID, category_id: uuid.UUID) -> CafeMenuCategory:
    cat = session.get(CafeMenuCategory, category_id)
    if cat is None or cat.organization_id != org_id:
        raise not_found("Category")
    return cat


def _check_list_price(price: Decimal, list_price: Decimal | None) -> None:
    if list_price is not None and list_price <= price:
        raise AppError(422, "VALIDATION_ERROR", "The 'was' price must be higher than the price")


@router.post("/menu/items", status_code=201)
def create_item(body: ItemIn, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    _category(session, org_id, body.category_id)
    price = Decimal(str(body.price))
    list_price = Decimal(str(body.list_price)) if body.list_price is not None else None
    _check_list_price(price, list_price)
    item = CafeMenuItem(organization_id=org_id, category_id=body.category_id, name=body.name.strip(), description=body.description.strip(),
                        aliases=[a.strip()[:64] for a in body.aliases if a.strip()], price=price, list_price=list_price,
                        is_new=body.is_new, available=body.available, sort=body.sort)
    session.add(item)
    session.flush()
    service.audit(session, org_id, "menu_item", item.id, "menu_item.created", principal.actor, _item_out(item))
    session.commit()
    return _item_out(item)


class ItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=300)
    aliases: list[str] | None = Field(default=None, max_length=20)
    price: str | int | None = None
    list_price: str | int | None = None
    clear_list_price: bool = False
    is_new: bool | None = None
    available: bool | None = None
    sort: int | None = Field(default=None, ge=0, le=10000)

    @field_validator("price", "list_price", mode="before")
    @classmethod
    def _p(cls, v: Any, info: Any) -> Any:
        return None if v in (None, "") else format(_price(v, info.field_name), "f")


@router.patch("/menu/items/{item_id}")
def patch_item(item_id: uuid.UUID, body: ItemPatch, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    item = session.get(CafeMenuItem, item_id)
    if item is None or item.organization_id != principal.organization.id:
        raise not_found("Menu item")
    patch = body.model_dump(exclude_none=True, exclude={"clear_list_price"})
    if "category_id" in patch:
        _category(session, item.organization_id, patch["category_id"])
    before = {"price": money(item.price), "list_price": money(item.list_price) if item.list_price is not None else None}
    for k, v in patch.items():
        setattr(item, k, Decimal(str(v)) if k in ("price", "list_price") else v)
    if body.clear_list_price:
        item.list_price = None
    _check_list_price(item.price, item.list_price)
    service.audit(session, item.organization_id, "menu_item", item.id, "menu_item.updated", principal.actor,
                  {"before": before, "changes": {k: str(v) for k, v in patch.items()}, "clear_list_price": body.clear_list_price})
    session.commit()
    return _item_out(item)


# ---- orders ----------------------------------------------------------------------------------------------------
class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: uuid.UUID
    quantity: int = Field(ge=1, le=20)


def _order_out(session: Session, o: CafeOrder, seat_label: str | None = None) -> dict[str, Any]:
    lines = session.scalars(select(CafeOrderLine).where(CafeOrderLine.order_id == o.id).order_by(CafeOrderLine.name)).all()
    if seat_label is None:
        seat = session.get(CafeSeat, o.seat_id)
        seat_label = seat.label if seat else None
    return {"id": str(o.id), "number": o.number, "seat_label": seat_label, "status": o.status, "payment_status": o.payment_status,
            "payment_method": o.payment_method, "total": money(o.total), "note": o.note, "via": o.via, "created_by": o.created_by,
            "created_at": o.created_at.isoformat(), "paid_at": o.paid_at.isoformat() if o.paid_at else None,
            "lines": [{"item_id": str(x.item_id) if x.item_id else None, "name": x.name, "unit_price": money(x.unit_price),
                       "quantity": x.quantity, "line_total": money(x.line_total)} for x in lines]}


def price_lines(session: Session, org_id: uuid.UUID, lines: list[LineIn]) -> tuple[list[tuple[CafeMenuItem, int, Decimal]], Decimal]:
    """Each line with the menu's current price, and the total. Refuses unknown, sold-out or hidden items."""
    if not lines:
        raise OrderError("EMPTY_ORDER", "Add at least one item", 422)
    if len(lines) > MAX_LINES:
        raise OrderError("TOO_MANY_LINES", f"At most {MAX_LINES} different items per order", 422)
    merged: dict[uuid.UUID, int] = {}
    for line in lines:
        merged[line.item_id] = merged.get(line.item_id, 0) + line.quantity
    items = {i.id: i for i in session.scalars(select(CafeMenuItem).where(CafeMenuItem.organization_id == org_id,
                                                                          CafeMenuItem.id.in_(merged)))}
    active = {c.id for c in session.scalars(select(CafeMenuCategory).where(CafeMenuCategory.organization_id == org_id,
                                                                             CafeMenuCategory.active.is_(True)))}
    priced = []
    for item_id, qty in merged.items():
        item = items.get(item_id)
        if item is None:
            raise OrderError("ITEM_NOT_FOUND", "An item in the order is not on the menu", 422, {"item_id": str(item_id)})
        if not item.available or item.category_id not in active:
            raise OrderError("ITEM_UNAVAILABLE", f"{item.name} is not available right now", 409, {"item": item.name})
        if qty > 20:
            raise OrderError("QUANTITY_TOO_HIGH", f"At most 20 of {item.name} per order", 422, {"item": item.name})
        priced.append((item, qty, (item.price * qty).quantize(TWO, ROUND_HALF_UP)))
    return priced, sum((t for _, _, t in priced), Decimal("0.00"))


def place_order(session: Session, seat: CafeSeat, lines: list[LineIn], *, note: str, actor: str, via: str,
                idempotency_key: str | None) -> CafeOrder:
    org_id = seat.organization_id
    if idempotency_key:
        existing = session.scalar(select(CafeOrder).where(CafeOrder.organization_id == org_id, CafeOrder.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    if seat.status != "active":
        raise OrderError("SEAT_UNAVAILABLE", f"Seat {seat.label} is not in service", details={"seat": seat.label})
    for attempt in range(3):  # the order number is max+1; a concurrent order may take it first
        priced, total = price_lines(session, org_id, lines)
        cs = service.active_session(session, seat)
        number = (session.scalar(select(func.max(CafeOrder.number)).where(CafeOrder.organization_id == org_id)) or 0) + 1
        order = CafeOrder(organization_id=org_id, number=number, seat_id=seat.id, session_id=cs.id if cs else None, total=total,
                          note=note.strip()[:50], via=via, idempotency_key=idempotency_key or None, created_by=actor)
        session.add(order)
        session.flush()
        for item, qty, line_total in priced:
            session.add(CafeOrderLine(organization_id=org_id, order_id=order.id, item_id=item.id, name=item.name, unit_price=item.price,
                                      quantity=qty, line_total=line_total))
        service.audit(session, org_id, "order", order.id, "order.placed", actor,
                      {"number": number, "seat": seat.label, "total": money(total), "via": via,
                       "lines": [{"item": i.name, "unit_price": money(i.price), "quantity": q} for i, q, _ in priced]})
        try:
            session.commit()
            return order
        except IntegrityError:
            session.rollback()
            if idempotency_key:
                existing = session.scalar(select(CafeOrder).where(CafeOrder.organization_id == org_id,
                                                                  CafeOrder.idempotency_key == idempotency_key))
                if existing is not None:
                    return existing
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lines: list[LineIn] = Field(min_length=1, max_length=MAX_LINES)
    note: str = Field(default="", max_length=50)


@router.post("/seats/{seat_id}/orders", status_code=201)
def create_order(seat_id: uuid.UUID, body: OrderIn, principal: Principal = Depends(staff), session: Session = Depends(get_db),
                 idempotency_key: str | None = Header(default=None, max_length=128)):
    seat = service.find_seat(session, principal.organization.id, seat_id=seat_id)
    order = place_order(session, seat, body.lines, note=body.note, actor=principal.actor, via="seat", idempotency_key=idempotency_key)
    return _order_out(session, order, seat.label)


@router.get("/orders")
def list_orders(status: Literal["open", "all", "placed", "preparing", "delivered", "cancelled"] = "open",
                seat_id: uuid.UUID | None = None, limit: int = Query(default=50, ge=1, le=200),
                principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    q = (select(CafeOrder, CafeSeat.label).join(CafeSeat, CafeSeat.id == CafeOrder.seat_id)
         .where(CafeOrder.organization_id == principal.organization.id))
    if status == "open":
        q = q.where(CafeOrder.status.in_(("placed", "preparing")))
    elif status != "all":
        q = q.where(CafeOrder.status == status)
    if seat_id is not None:
        q = q.where(CafeOrder.seat_id == seat_id)
    rows = session.execute(q.order_by(CafeOrder.created_at.desc()).limit(limit)).all()
    return {"items": [_order_out(session, o, label) for o, label in rows]}


def _order(session: Session, org_id: uuid.UUID, order_id: uuid.UUID) -> CafeOrder:
    o = session.get(CafeOrder, order_id)
    if o is None or o.organization_id != org_id:
        raise not_found("Order")
    return o


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["preparing", "delivered", "cancelled"]


@router.post("/orders/{order_id}/status")
def set_status(order_id: uuid.UUID, body: StatusIn, principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    o = _order(session, principal.organization.id, order_id)
    if o.status == body.status:
        return _order_out(session, o)
    if body.status not in NEXT_STATUS.get(o.status, set()):
        raise OrderError("BAD_TRANSITION", f"Order #{o.number} is {o.status} and cannot become {body.status}")
    if body.status == "cancelled" and o.payment_status == "paid":
        raise OrderError("ORDER_PAID", f"Order #{o.number} is already paid; refund it at the counter first")
    before = o.status
    o.status = body.status
    service.audit(session, o.organization_id, "order", o.id, f"order.{body.status}", principal.actor, {"from": before, "number": o.number})
    session.commit()
    return _order_out(session, o)


@router.post("/orders/{order_id}/paid")
def mark_paid(order_id: uuid.UUID, principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    """Staff took payment at the counter. Recorded once, with who and when; nothing is charged by the system."""
    o = _order(session, principal.organization.id, order_id)
    if o.payment_status == "paid":
        return _order_out(session, o)
    if o.status == "cancelled":
        raise OrderError("ORDER_CANCELLED", f"Order #{o.number} was cancelled")
    o.payment_status, o.paid_by, o.paid_at = "paid", principal.actor, utcnow()
    service.audit(session, o.organization_id, "order", o.id, "order.paid", principal.actor,
                  {"number": o.number, "total": money(o.total), "method": o.payment_method})
    session.commit()
    return _order_out(session, o)
