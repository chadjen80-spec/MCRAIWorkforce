"""/v1/qtc: Quote-to-Cash API (Phase A: customers, contacts, service catalogue, price lists, audit).

Authorization:
- read: anyone in the organization (signed-in users and service keys);
- create / change customers, services and draft price lists: signed-in users only (service keys get 403
  until scoped integration credentials exist, Phase E);
- approve (activate) or archive a price list, change the default: owner or admin.
Approved price lists are frozen; to change prices, copy one to a new draft and approve that.
Every list is paginated, every change is audited in the same transaction, and creations accept an
Idempotency-Key header.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db, utcnow
from app.deps import Principal, get_principal, require_admin
from app.errors import AppError, conflict, forbidden, not_found
from app.qtc import audit, numbering
from app.qtc import schemas as s
from app.qtc.models import Customer, CustomerContact, PriceList, PriceListItem, QtcAuditEvent, ServiceItem

router = APIRouter(prefix="/v1/qtc", tags=["quote-to-cash"])


def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    """Sales and finance records are changed by people; integration keys are read-only for now."""
    if principal.user is None:
        raise forbidden("Service keys cannot change sales or finance records")
    return principal


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ---- customers -------------------------------------------------------------------------
def _customer(session: Session, org_id: uuid.UUID, customer_id: uuid.UUID) -> Customer:
    c = session.scalar(select(Customer).where(Customer.id == customer_id, Customer.organization_id == org_id))
    if c is None:
        raise not_found("Customer")
    return c


def _customer_out(session: Session, c: Customer) -> s.CustomerOut:
    contacts = session.scalars(
        select(CustomerContact).where(CustomerContact.customer_id == c.id).order_by(CustomerContact.is_primary.desc(), CustomerContact.created_at)
    ).all()
    out = s.CustomerOut.model_validate(c)
    out.contacts = [s.ContactOut.model_validate(x) for x in contacts]
    return out


def _next_customer_code(session: Session, org_id: uuid.UUID) -> str:
    """C0001, C0002 … from a locked per-organization counter (no two requests get the same code)."""

    def start() -> int:  # first use: continue after any C-codes that already exist
        codes = session.scalars(select(Customer.code).where(Customer.organization_id == org_id, Customer.code.like("C%"))).all()
        numbers = [int(m[1]) for c in codes if (m := re.fullmatch(r"C(\d+)", c))]
        return (max(numbers) + 1) if numbers else 1

    return f"C{numbering.take(session, org_id, 'customer_code', start=start):04d}"


def _set_primary(session: Session, customer_id: uuid.UUID, keep: uuid.UUID) -> None:
    session.execute(update(CustomerContact).where(CustomerContact.customer_id == customer_id, CustomerContact.id != keep)
                    .values(is_primary=False))


@router.get("/customers", response_model=s.CustomerList)
def list_customers(
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default="active", pattern="^(active|archived|all)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    where = [Customer.organization_id == principal.organization.id]
    if status != "all":
        where.append(Customer.status == status)
    if q and q.strip():
        pat = _like(q.strip())
        where.append(or_(Customer.name.ilike(pat, escape="\\"), Customer.legal_name.ilike(pat, escape="\\"),
                         Customer.code.ilike(pat, escape="\\"), Customer.tax_id.ilike(pat, escape="\\")))
    total = session.scalar(select(func.count()).select_from(Customer).where(*where)) or 0
    rows = session.scalars(select(Customer).where(*where).order_by(Customer.name).limit(limit).offset(offset)).all()
    return s.CustomerList(items=[_customer_out(session, c) for c in rows], total=total, limit=limit, offset=offset)


@router.post("/customers", response_model=s.CustomerOut, status_code=201)
def create_customer(
    body: s.CustomerCreate,
    response: Response,
    principal: Principal = Depends(require_user),
    session: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    org_id = principal.organization.id
    if idempotency_key:
        existing = session.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.idempotency_key == idempotency_key))
        if existing is not None:
            response.status_code = 200
            return _customer_out(session, existing)
    for attempt in range(5):  # a generated code only collides with a code someone typed by hand: take the next one
        code = body.code or _next_customer_code(session, org_id)
        customer = Customer(
            organization_id=org_id, code=code, name=body.name.strip(), legal_name=body.legal_name.strip(),
            customer_type=body.customer_type, tax_id=body.tax_id, branch=body.branch, address=body.address,
            email=body.email.strip(), phone=body.phone.strip(), payment_terms_days=body.payment_terms_days,
            notes=body.notes, idempotency_key=idempotency_key or None, created_by=principal.actor,
        )
        try:
            with session.begin_nested():
                session.add(customer)
                session.flush()
        except IntegrityError:
            if idempotency_key:
                existing = session.scalar(select(Customer).where(Customer.organization_id == org_id, Customer.idempotency_key == idempotency_key))
                if existing is not None:
                    response.status_code = 200
                    return _customer_out(session, existing)
            if body.code or attempt == 4:
                raise conflict(f"Customer code {code} is already used") from None
            continue
        break
    primary_seen = False
    for c in body.contacts:
        primary = c.is_primary and not primary_seen
        primary_seen = primary_seen or primary
        session.add(CustomerContact(organization_id=org_id, customer_id=customer.id, **{**c.model_dump(), "is_primary": primary}))
    audit.record(session, org_id, "customer", customer.id, "customer.created", principal.actor,
                 {"code": customer.code, "name": customer.name, "contacts": len(body.contacts)})
    session.commit()
    return _customer_out(session, customer)


@router.get("/customers/{customer_id}", response_model=s.CustomerOut)
def get_customer(customer_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return _customer_out(session, _customer(session, principal.organization.id, customer_id))


@router.patch("/customers/{customer_id}", response_model=s.CustomerOut)
def patch_customer(customer_id: uuid.UUID, body: s.CustomerPatch, principal: Principal = Depends(require_user),
                   session: Session = Depends(get_db)):
    org_id = principal.organization.id
    c = _customer(session, org_id, customer_id)
    patch = body.model_dump(exclude_unset=True)
    if "tax_id" in body.model_fields_set and body.tax_id is None:
        patch["tax_id"] = None
    for key in ("name", "code"):
        if patch.get(key) is None:
            patch.pop(key, None)
    diff = audit.changes(c, patch)
    if diff:
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise conflict("That customer code is already used") from None
        audit.record(session, org_id, "customer", c.id, "customer.updated", principal.actor, diff)
    session.commit()
    return _customer_out(session, c)


@router.post("/customers/{customer_id}/contacts", response_model=s.ContactOut, status_code=201)
def add_contact(customer_id: uuid.UUID, body: s.ContactIn, principal: Principal = Depends(require_user),
                session: Session = Depends(get_db)):
    org_id = principal.organization.id
    c = _customer(session, org_id, customer_id)
    contact = CustomerContact(organization_id=org_id, customer_id=c.id, **body.model_dump())
    session.add(contact)
    session.flush()
    if contact.is_primary:
        _set_primary(session, c.id, contact.id)
    audit.record(session, org_id, "customer", c.id, "contact.added", principal.actor, {"contact_id": contact.id, "name": contact.name})
    session.commit()
    return s.ContactOut.model_validate(contact)


@router.patch("/customers/{customer_id}/contacts/{contact_id}", response_model=s.ContactOut)
def patch_contact(customer_id: uuid.UUID, contact_id: uuid.UUID, body: s.ContactPatch,
                  principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    c = _customer(session, org_id, customer_id)
    contact = session.scalar(select(CustomerContact).where(CustomerContact.id == contact_id, CustomerContact.customer_id == c.id,
                                                           CustomerContact.organization_id == org_id))
    if contact is None:
        raise not_found("Contact")
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    diff = audit.changes(contact, patch)
    if diff:
        if contact.is_primary:
            _set_primary(session, c.id, contact.id)
        # consent changes are recorded explicitly: they decide which channels reminders may use
        audit.record(session, org_id, "customer", c.id, "contact.updated", principal.actor, {"contact_id": contact.id, **diff})
    session.commit()
    return s.ContactOut.model_validate(contact)


# ---- services --------------------------------------------------------------------------
def _service(session: Session, org_id: uuid.UUID, service_id: uuid.UUID) -> ServiceItem:
    svc = session.scalar(select(ServiceItem).where(ServiceItem.id == service_id, ServiceItem.organization_id == org_id))
    if svc is None:
        raise not_found("Service")
    return svc


def _default_prices(session: Session, org_id: uuid.UUID) -> dict[uuid.UUID, object]:
    default = session.scalar(select(PriceList).where(PriceList.organization_id == org_id, PriceList.is_default.is_(True),
                                                     PriceList.status == "active"))
    if default is None:
        return {}
    rows = session.execute(select(PriceListItem.service_id, PriceListItem.unit_price).where(PriceListItem.price_list_id == default.id))
    return {sid: price for sid, price in rows}


def _service_out(svc: ServiceItem, prices: dict[uuid.UUID, object]) -> s.ServiceOut:
    out = s.ServiceOut.model_validate(svc)
    out.default_price = prices.get(svc.id)  # type: ignore[assignment]
    return out


@router.get("/services", response_model=s.ServiceList)
def list_services(
    q: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default="active", pattern="^(active|archived|all)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    org_id = principal.organization.id
    where = [ServiceItem.organization_id == org_id]
    if status != "all":
        where.append(ServiceItem.status == status)
    if q and q.strip():
        pat = _like(q.strip())
        where.append(or_(ServiceItem.name.ilike(pat, escape="\\"), ServiceItem.name_en.ilike(pat, escape="\\"),
                         ServiceItem.code.ilike(pat, escape="\\"), ServiceItem.category.ilike(pat, escape="\\")))
    total = session.scalar(select(func.count()).select_from(ServiceItem).where(*where)) or 0
    rows = session.scalars(select(ServiceItem).where(*where).order_by(ServiceItem.category, ServiceItem.code).limit(limit).offset(offset)).all()
    prices = _default_prices(session, org_id)
    return s.ServiceList(items=[_service_out(x, prices) for x in rows], total=total, limit=limit, offset=offset)


@router.post("/services", response_model=s.ServiceOut, status_code=201)
def create_service(body: s.ServiceCreate, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    svc = ServiceItem(organization_id=org_id, **body.model_dump())
    try:
        with session.begin_nested():
            session.add(svc)
            session.flush()
    except IntegrityError:
        raise conflict(f"Service code {body.code} is already used") from None
    audit.record(session, org_id, "service", svc.id, "service.created", principal.actor, {"code": svc.code, "name": svc.name})
    session.commit()
    return _service_out(svc, {})


@router.get("/services/{service_id}", response_model=s.ServiceOut)
def get_service(service_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    return _service_out(_service(session, org_id, service_id), _default_prices(session, org_id))


@router.patch("/services/{service_id}", response_model=s.ServiceOut)
def patch_service(service_id: uuid.UUID, body: s.ServicePatch, principal: Principal = Depends(require_user),
                  session: Session = Depends(get_db)):
    org_id = principal.organization.id
    svc = _service(session, org_id, service_id)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    diff = audit.changes(svc, patch)
    if diff:
        audit.record(session, org_id, "service", svc.id, "service.updated", principal.actor, diff)
    session.commit()
    return _service_out(svc, _default_prices(session, org_id))


# ---- price lists -----------------------------------------------------------------------
def _price_list(session: Session, org_id: uuid.UUID, price_list_id: uuid.UUID, *, lock: bool = False) -> PriceList:
    stmt = select(PriceList).where(PriceList.id == price_list_id, PriceList.organization_id == org_id)
    pl = session.scalar(stmt.with_for_update() if lock else stmt)
    if pl is None:
        raise not_found("Price list")
    return pl


def _price_list_out(session: Session, pl: PriceList) -> s.PriceListOut:
    rows = session.execute(
        select(PriceListItem, ServiceItem).join(ServiceItem, ServiceItem.id == PriceListItem.service_id)
        .where(PriceListItem.price_list_id == pl.id).order_by(ServiceItem.code)
    ).all()
    return s.PriceListOut(
        id=pl.id, name=pl.name, currency=pl.currency, prices_include_vat=pl.prices_include_vat, vat_rate=pl.vat_rate,
        valid_from=pl.valid_from, valid_to=pl.valid_to, status=pl.status, is_default=pl.is_default,
        approved_by=pl.approved_by, approved_at=pl.approved_at, created_at=pl.created_at,
        items=[s.PriceItemOut(service_id=svc.id, service_code=svc.code, service_name=svc.name, unit=svc.unit,
                              unit_price=item.unit_price, notes=item.notes) for item, svc in rows],
    )


def _require_draft(pl: PriceList) -> None:
    if pl.status != "draft":
        raise AppError(409, "PRICE_LIST_LOCKED", "An approved price list cannot be changed; copy it to a new draft instead")


def _check_dates(valid_from: date | None, valid_to: date | None) -> None:
    if valid_from and valid_to and valid_to < valid_from:
        raise AppError(422, "VALIDATION_ERROR", "valid_to is before valid_from")


@router.get("/price-lists", response_model=s.PriceListList)
def list_price_lists(
    status: str | None = Query(default="all", pattern="^(draft|active|archived|all)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    where = [PriceList.organization_id == principal.organization.id]
    if status != "all":
        where.append(PriceList.status == status)
    total = session.scalar(select(func.count()).select_from(PriceList).where(*where)) or 0
    rows = session.scalars(select(PriceList).where(*where).order_by(PriceList.is_default.desc(), PriceList.created_at.desc())
                           .limit(limit).offset(offset)).all()
    return s.PriceListList(items=[_price_list_out(session, pl) for pl in rows], total=total, limit=limit, offset=offset)


@router.post("/price-lists", response_model=s.PriceListOut, status_code=201)
def create_price_list(body: s.PriceListCreate, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    _check_dates(body.valid_from, body.valid_to)
    pl = PriceList(organization_id=org_id, **body.model_dump())
    try:
        with session.begin_nested():
            session.add(pl)
            session.flush()
    except IntegrityError:
        raise conflict(f"A price list named {body.name!r} already exists") from None
    audit.record(session, org_id, "price_list", pl.id, "price_list.created", principal.actor, body.model_dump())
    session.commit()
    return _price_list_out(session, pl)


@router.get("/price-lists/{price_list_id}", response_model=s.PriceListOut)
def get_price_list(price_list_id: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return _price_list_out(session, _price_list(session, principal.organization.id, price_list_id))


@router.patch("/price-lists/{price_list_id}", response_model=s.PriceListOut)
def patch_price_list(price_list_id: uuid.UUID, body: s.PriceListPatch, principal: Principal = Depends(require_user),
                     session: Session = Depends(get_db)):
    org_id = principal.organization.id
    pl = _price_list(session, org_id, price_list_id, lock=True)
    _require_draft(pl)
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None or k in ("valid_from", "valid_to")}
    _check_dates(patch.get("valid_from", pl.valid_from), patch.get("valid_to", pl.valid_to))
    diff = audit.changes(pl, patch)
    if diff:
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise conflict("A price list with that name already exists") from None
        audit.record(session, org_id, "price_list", pl.id, "price_list.updated", principal.actor, diff)
    session.commit()
    return _price_list_out(session, pl)


@router.put("/price-lists/{price_list_id}/items/{service_id}", response_model=s.PriceListOut)
def set_price(price_list_id: uuid.UUID, service_id: uuid.UUID, body: s.PriceItemIn,
              principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    pl = _price_list(session, org_id, price_list_id, lock=True)
    _require_draft(pl)
    svc = _service(session, org_id, service_id)
    item = session.scalar(select(PriceListItem).where(PriceListItem.price_list_id == pl.id, PriceListItem.service_id == svc.id))
    before = item.unit_price if item else None
    if item is None:
        item = PriceListItem(organization_id=org_id, price_list_id=pl.id, service_id=svc.id, unit_price=body.unit_price, notes=body.notes)
        session.add(item)
    else:
        item.unit_price, item.notes = body.unit_price, body.notes
    audit.record(session, org_id, "price_list", pl.id, "price_list.price_set", principal.actor,
                 {"service": svc.code, "from": before, "to": body.unit_price})
    session.commit()
    return _price_list_out(session, pl)


@router.delete("/price-lists/{price_list_id}/items/{service_id}", response_model=s.PriceListOut)
def remove_price(price_list_id: uuid.UUID, service_id: uuid.UUID, principal: Principal = Depends(require_user),
                 session: Session = Depends(get_db)):
    org_id = principal.organization.id
    pl = _price_list(session, org_id, price_list_id, lock=True)
    _require_draft(pl)
    item = session.scalar(select(PriceListItem).where(PriceListItem.price_list_id == pl.id, PriceListItem.service_id == service_id))
    if item is None:
        raise not_found("Price")
    session.delete(item)
    audit.record(session, org_id, "price_list", pl.id, "price_list.price_removed", principal.actor, {"service_id": service_id})
    session.commit()
    return _price_list_out(session, pl)


@router.post("/price-lists/{price_list_id}/activate", response_model=s.PriceListOut)
def activate_price_list(price_list_id: uuid.UUID, make_default: bool = Query(default=True),
                        principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    """Approve a draft rate card. Only approved price lists may price quotations."""
    if principal.user is None:
        raise forbidden("Price lists are approved by a signed-in owner or admin")
    org_id = principal.organization.id
    pl = _price_list(session, org_id, price_list_id, lock=True)
    _require_draft(pl)
    if not session.scalar(select(func.count()).select_from(PriceListItem).where(PriceListItem.price_list_id == pl.id)):
        raise AppError(422, "EMPTY_PRICE_LIST", "Add at least one price before approving the price list")
    pl.status, pl.approved_by, pl.approved_at = "active", principal.actor, utcnow()
    has_default = session.scalar(select(func.count()).select_from(PriceList).where(PriceList.organization_id == org_id,
                                                                                   PriceList.is_default.is_(True)))
    if make_default or not has_default:
        session.execute(update(PriceList).where(PriceList.organization_id == org_id, PriceList.id != pl.id).values(is_default=False))
        session.flush()
        pl.is_default = True
    audit.record(session, org_id, "price_list", pl.id, "price_list.approved", principal.actor, {"default": pl.is_default})
    session.commit()
    return _price_list_out(session, pl)


@router.post("/price-lists/{price_list_id}/archive", response_model=s.PriceListOut)
def archive_price_list(price_list_id: uuid.UUID, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    pl = _price_list(session, org_id, price_list_id, lock=True)
    if pl.status == "archived":
        return _price_list_out(session, pl)
    pl.status, pl.is_default = "archived", False
    audit.record(session, org_id, "price_list", pl.id, "price_list.archived", principal.actor)
    session.commit()
    return _price_list_out(session, pl)


@router.post("/price-lists/{price_list_id}/copy", response_model=s.PriceListOut, status_code=201)
def copy_price_list(price_list_id: uuid.UUID, name: str = Query(min_length=1, max_length=120),
                    principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    """Start a new draft from an existing price list (the way to change approved prices)."""
    org_id = principal.organization.id
    src = _price_list(session, org_id, price_list_id)
    pl = PriceList(organization_id=org_id, name=name, currency=src.currency, prices_include_vat=src.prices_include_vat,
                   vat_rate=src.vat_rate, valid_from=src.valid_from, valid_to=src.valid_to)
    try:
        with session.begin_nested():
            session.add(pl)
            session.flush()
    except IntegrityError:
        raise conflict(f"A price list named {name!r} already exists") from None
    for item in session.scalars(select(PriceListItem).where(PriceListItem.price_list_id == src.id)):
        session.add(PriceListItem(organization_id=org_id, price_list_id=pl.id, service_id=item.service_id,
                                  unit_price=item.unit_price, notes=item.notes))
    audit.record(session, org_id, "price_list", pl.id, "price_list.copied", principal.actor, {"from": src.id})
    session.commit()
    return _price_list_out(session, pl)


# ---- audit -----------------------------------------------------------------------------
@router.get("/audit-events", response_model=s.AuditEventList)
def list_audit_events(
    entity_type: str | None = Query(default=None, max_length=40),
    entity_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_admin),
    session: Session = Depends(get_db),
):
    where = [QtcAuditEvent.organization_id == principal.organization.id]
    if entity_type:
        where.append(QtcAuditEvent.entity_type == entity_type)
    if entity_id:
        where.append(QtcAuditEvent.entity_id == entity_id)
    total = session.scalar(select(func.count()).select_from(QtcAuditEvent).where(*where)) or 0
    rows = session.scalars(select(QtcAuditEvent).where(*where).order_by(QtcAuditEvent.created_at.desc()).limit(limit).offset(offset)).all()
    return s.AuditEventList(items=[s.AuditEventOut.model_validate(x) for x in rows], total=total, limit=limit, offset=offset)
