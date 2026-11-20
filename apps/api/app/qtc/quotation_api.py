"""/v1/qtc quotations and seller settings (Phase B).

Who may do what:
- create and edit drafts, submit, record sending, record acceptance or decline, revise: signed-in users;
- approve (or send back) a submitted quotation: owner or admin, signed in;
- read and download: anyone in the organization, except that drafts and unapproved revisions are only
  rendered for signed-in users (never for integration keys), watermarked "draft, not approved".
Nothing here sends anything outside the system: "send" records how a person delivered the approved
document. External delivery stays disabled until consent, credential and approval gates exist (Phase E).
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.responses import Response as RawResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db, utcnow
from app.deps import Principal, get_principal, require_admin
from app.errors import AppError, forbidden, not_found
from app.qtc import audit, events
from app.qtc import documents as docs
from app.qtc import quotations as engine
from app.qtc import schemas as s
from app.qtc.api import require_user
from app.qtc.models import (
    Customer,
    CustomerContact,
    QtcSettings,
    Quotation,
    QuotationApproval,
    QuotationRevision,
    QuotationStatus,
)

router = APIRouter(prefix="/v1/qtc", tags=["quote-to-cash"])
St = QuotationStatus


# ---- settings ------------------------------------------------------------------------------
@router.get("/settings", response_model=s.SettingsOut)
def get_settings(principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return s.SettingsOut.model_validate(engine.settings_for(session, principal.organization.id))


@router.put("/settings", response_model=s.SettingsOut)
def put_settings(body: s.SettingsIn, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    row = session.get(QtcSettings, org_id)
    if row is None:
        row = QtcSettings(organization_id=org_id)
        session.add(row)
    diff = audit.changes(row, {**body.model_dump(), "updated_by": principal.actor})
    diff.pop("updated_by", None)
    if diff:
        audit.record(session, org_id, "settings", org_id, "settings.updated", principal.actor, diff)
    session.commit()
    return s.SettingsOut.model_validate(row)


# ---- helpers --------------------------------------------------------------------------------
def _quotation(session: Session, org_id: uuid.UUID, qid: uuid.UUID, *, lock: bool = False) -> Quotation:
    stmt = select(Quotation).where(Quotation.id == qid, Quotation.organization_id == org_id)
    q = session.scalar(stmt.with_for_update() if lock else stmt)
    if q is None:
        raise not_found("Quotation")
    return q


def _claim(session: Session, q: Quotation, allowed: set[str], action: str) -> None:
    """Atomically take the right to change this quotation: `UPDATE … WHERE status IN (allowed)`.

    The row lock from `_quotation(lock=True)` already serializes this on PostgreSQL; the conditional
    update makes it hold on every database (SQLite ignores FOR UPDATE), so of several concurrent
    approvals, sends or acceptances exactly one succeeds and the others get 409.
    """
    res = session.execute(update(Quotation).where(Quotation.id == q.id, Quotation.status.in_(allowed)).values(updated_at=utcnow()))
    if res.rowcount != 1:  # type: ignore[attr-defined]
        session.rollback()
        session.refresh(q)
        raise engine.invalid_state(q, action)


def _out(session: Session, q: Quotation, matched: list[dict] | None = None) -> s.QuotationOut:
    customer = session.get(Customer, q.customer_id)
    lines = engine.lines_of(session, q)
    return s.QuotationOut(
        id=q.id, number=q.number, revision=q.revision, status=engine.effective_status(q),
        customer={"id": customer.id, "code": customer.code, "name": customer.name} if customer else {},
        contact_id=q.contact_id, price_list_id=q.price_list_id, title=q.title, brief=q.brief, currency=q.currency,
        prices_include_vat=q.prices_include_vat, vat_rate=q.vat_rate, issue_date=q.issue_date, valid_until=q.valid_until,
        notes=q.notes, terms=q.terms, subtotal=q.subtotal, discount_total=q.discount_total, vat_total=q.vat_total,
        grand_total=q.grand_total, amount_in_words=engine.thai_baht_text(q.grand_total),
        missing_prices=sum(1 for x in lines if x.unit_price is None), created_by=q.created_by, submitted_by=q.submitted_by,
        approved_by=q.approved_by, approved_at=q.approved_at, sent_at=q.sent_at, sent_method=q.sent_method,
        accepted_at=q.accepted_at, accepted_revision=q.accepted_revision, acceptance=q.acceptance,
        created_at=q.created_at, updated_at=q.updated_at,
        lines=[s.QuotationLineOut(position=x.position, service_id=x.service_id, description=x.description, unit=x.unit,
                                  quantity=x.quantity, unit_price=x.unit_price, price_source=x.price_source,
                                  discount_percent=x.discount_percent, discount_amount=x.discount_amount,
                                  vat_exempt=x.vat_exempt, line_net=x.line_net, line_vat=x.line_vat, line_total=x.line_total)
               for x in lines],
        matched=matched or [],
    )


def _check_contact(session: Session, org_id: uuid.UUID, customer_id: uuid.UUID, contact_id: uuid.UUID | None) -> None:
    if contact_id is None:
        return
    ok = session.scalar(select(CustomerContact.id).where(CustomerContact.id == contact_id, CustomerContact.customer_id == customer_id,
                                                         CustomerContact.organization_id == org_id))
    if ok is None:
        raise AppError(422, "VALIDATION_ERROR", "That contact does not belong to this customer")


def _manual_prices(lines: list[Any]) -> list[dict[str, Any]]:
    return [{"position": x.position, "description": x.description, "unit_price": x.unit_price} for x in lines if x.price_source == "manual"]


# ---- list / create / read / edit --------------------------------------------------------------
@router.get("/quotations", response_model=s.QuotationList)
def list_quotations(
    status: str | None = Query(default=None, pattern="^(draft|pending_approval|approved|sent|accepted|declined|cancelled|expired)$"),
    customer_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    today = date.today()
    where: list[Any] = [Quotation.organization_id == principal.organization.id]
    if status == "expired":
        where += [Quotation.status.in_([St.APPROVED, St.SENT]), Quotation.valid_until < today]
    elif status in (St.APPROVED, St.SENT):
        where += [Quotation.status == status, Quotation.valid_until >= today]
    elif status:
        where.append(Quotation.status == status)
    if customer_id:
        where.append(Quotation.customer_id == customer_id)
    if q and q.strip():
        where.append(Quotation.number.ilike(f"%{q.strip()}%") | Quotation.title.ilike(f"%{q.strip()}%"))
    total = session.scalar(select(func.count()).select_from(Quotation).where(*where)) or 0
    rows = session.scalars(select(Quotation).where(*where).order_by(Quotation.created_at.desc()).limit(limit).offset(offset)).all()
    return s.QuotationList(items=[_out(session, x) for x in rows], total=total, limit=limit, offset=offset)


@router.post("/quotations", response_model=s.QuotationOut, status_code=201)
def create_quotation(
    body: s.QuotationCreate,
    response: Response,
    principal: Principal = Depends(require_user),
    session: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    org_id = principal.organization.id
    if idempotency_key:
        existing = session.scalar(select(Quotation).where(Quotation.organization_id == org_id, Quotation.idempotency_key == idempotency_key))
        if existing is not None:
            response.status_code = 200
            return _out(session, existing)
    customer = session.scalar(select(Customer).where(Customer.id == body.customer_id, Customer.organization_id == org_id))
    if customer is None:
        raise not_found("Customer")
    if customer.status != "active":
        raise AppError(422, "CUSTOMER_ARCHIVED", "This customer is archived")
    _check_contact(session, org_id, customer.id, body.contact_id)
    pl = engine.approved_price_list(session, org_id, body.price_list_id)
    issue = date.today()
    valid_until = body.valid_until or engine.default_valid_until(session, org_id, issue)
    if valid_until < issue:
        raise AppError(422, "VALIDATION_ERROR", "valid_until is in the past")

    matched: list[dict] = []
    if body.lines is not None:
        raw_lines = [x.model_dump() for x in body.lines]
    elif body.match_brief and body.brief.strip():
        found = engine.match_brief(session, org_id, body.brief)
        matched = [{"service_id": str(svc.id), "code": svc.code, "name": svc.name, "matched": word} for svc, word in found]
        raw_lines = [{"service_id": svc.id} for svc, _ in found]
    else:
        raw_lines = []

    q = Quotation(
        organization_id=org_id, number=engine.new_number(session, org_id, issue), customer_id=customer.id, contact_id=body.contact_id,
        price_list_id=pl.id, title=body.title.strip(), brief=body.brief, currency=pl.currency, prices_include_vat=pl.prices_include_vat,
        vat_rate=pl.vat_rate, issue_date=issue, valid_until=valid_until, notes=body.notes,
        terms=body.terms if body.terms is not None else engine.settings_for(session, org_id).quotation_terms,
        created_by=principal.actor, idempotency_key=idempotency_key or None,
    )
    try:
        with session.begin_nested():
            session.add(q)
            session.flush()
    except IntegrityError:
        existing = session.scalar(select(Quotation).where(Quotation.organization_id == org_id, Quotation.idempotency_key == idempotency_key))
        if idempotency_key and existing is not None:
            response.status_code = 200
            return _out(session, existing)
        raise
    lines = engine.replace_lines(session, q, pl, raw_lines)
    audit.record(session, org_id, "quotation", q.id, "quotation.created", principal.actor,
                 {"number": q.number, "customer": customer.code, "lines": len(lines), "matched": [m["code"] for m in matched],
                  "manual_prices": _manual_prices(lines)})
    session.commit()
    return _out(session, q, matched)


@router.get("/quotations/{qid}", response_model=s.QuotationOut)
def get_quotation(qid: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    return _out(session, _quotation(session, principal.organization.id, qid))


@router.patch("/quotations/{qid}", response_model=s.QuotationOut)
def patch_quotation(qid: uuid.UUID, body: s.QuotationPatch, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if q.status not in engine.EDITABLE:
        raise engine.invalid_state(q, "edit")
    _claim(session, q, set(engine.EDITABLE), "edit")
    fields = body.model_dump(exclude_unset=True, exclude={"lines"})
    if "contact_id" in fields:
        _check_contact(session, org_id, q.customer_id, fields["contact_id"])
    if fields.get("valid_until") and fields["valid_until"] < q.issue_date:
        raise AppError(422, "VALIDATION_ERROR", "valid_until is before the issue date")
    fields = {k: v for k, v in fields.items() if v is not None or k == "contact_id"}
    diff = audit.changes(q, fields)
    if body.lines is not None:
        pl = engine.approved_price_list(session, org_id, q.price_list_id)
        lines = engine.replace_lines(session, q, pl, [x.model_dump() for x in body.lines])
        diff["lines"] = {"to": len(lines), "manual_prices": _manual_prices(lines)}
        diff["grand_total"] = {"to": q.grand_total}
    if diff:
        audit.record(session, org_id, "quotation", q.id, "quotation.updated", principal.actor, diff)
    session.commit()
    return _out(session, q)


# ---- state changes (each locks the row and checks the current state) -----------------------------
@router.post("/quotations/{qid}/submit", response_model=s.QuotationOut)
def submit_quotation(qid: uuid.UUID, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if q.status != St.DRAFT:
        raise engine.invalid_state(q, "submit")
    _claim(session, q, {St.DRAFT}, "submit")
    lines = engine.lines_of(session, q)
    if not lines:
        raise AppError(422, "NO_LINES", "Add at least one line before submitting")
    missing = [x.position for x in lines if x.unit_price is None]
    if missing:
        raise AppError(422, "PRICE_REQUIRED", "A person must enter the price of every line before submitting",
                       {"lines": missing})
    if q.valid_until < date.today():
        raise AppError(422, "VALIDATION_ERROR", "valid_until is in the past")
    customer = session.get(Customer, q.customer_id)
    assert customer is not None
    # Recompute from the stored lines so the frozen revision always matches the arithmetic rules.
    engine.apply_totals(q, engine.compute(lines, vat_rate=q.vat_rate, prices_include_vat=q.prices_include_vat))
    q.status, q.submitted_by = St.PENDING_APPROVAL, principal.actor
    session.add(QuotationRevision(organization_id=org_id, quotation_id=q.id, revision=q.revision,
                                  snapshot=engine.snapshot(q, lines, customer), grand_total=q.grand_total,
                                  reason="submitted" if q.revision == 1 else "revision submitted", created_by=principal.actor))
    audit.record(session, org_id, "quotation", q.id, "quotation.submitted", principal.actor, {"revision": q.revision, "total": q.grand_total})
    events.emit(session, org_id, "quotation.submitted", f"quotation.submitted:{q.id}:r{q.revision}",
                events.quotation_data(session, q, submitted_by=principal.actor))
    session.commit()
    return _out(session, q)


@router.post("/quotations/{qid}/approve", response_model=s.QuotationOut)
def approve_quotation(qid: uuid.UUID, body: s.ApproveIn, principal: Principal = Depends(require_admin), session: Session = Depends(get_db)):
    if principal.user is None:
        raise forbidden("Quotations are approved by a signed-in owner or admin")
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if q.status != St.PENDING_APPROVAL:
        raise engine.invalid_state(q, "approve")
    _claim(session, q, {St.PENDING_APPROVAL}, "approve")
    session.add(QuotationApproval(organization_id=org_id, quotation_id=q.id, revision=q.revision, decision=body.decision,
                                  comment=body.comment, decided_by=principal.actor))
    if body.decision == "approved":
        q.status, q.approved_by, q.approved_at = St.APPROVED, principal.actor, utcnow()
    else:
        # Back to the author with the comment. The version the approver saw stays frozen as this revision;
        # the corrected quotation is submitted as the next one.
        q.status, q.submitted_by = St.DRAFT, None
        q.revision += 1
    audit.record(session, org_id, "quotation", q.id, f"quotation.{body.decision}", principal.actor,
                 {"revision": q.revision, "comment": body.comment, "self_approved": q.submitted_by == principal.actor})
    decided_revision = q.revision if body.decision == "approved" else q.revision - 1
    events.emit(session, org_id, "quotation.decided", f"quotation.decided:{q.id}:r{decided_revision}",
                events.quotation_data(session, q, decision=body.decision, decided_by=principal.actor, comment=body.comment,
                                      decided_revision=decided_revision))
    session.commit()
    return _out(session, q)


@router.post("/quotations/{qid}/send", response_model=s.QuotationOut)
def send_quotation(qid: uuid.UUID, body: s.SendIn, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    """Record that a person gave the approved document to the customer. Nothing is sent by the system."""
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if engine.effective_status(q) != St.APPROVED:
        raise engine.invalid_state(q, "send")
    _claim(session, q, {St.APPROVED}, "send")
    q.status, q.sent_at, q.sent_by, q.sent_method = St.SENT, utcnow(), principal.actor, body.method
    audit.record(session, org_id, "quotation", q.id, "quotation.sent", principal.actor,
                 {"revision": q.revision, "method": body.method, "note": body.note})
    session.commit()
    return _out(session, q)


@router.post("/quotations/{qid}/accept", response_model=s.QuotationOut)
def accept_quotation(qid: uuid.UUID, body: s.AcceptIn, response: Response, principal: Principal = Depends(require_user),
                     session: Session = Depends(get_db)):
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if q.status == St.ACCEPTED and (body.revision is None or body.revision == q.accepted_revision):
        response.status_code = 200  # the same acceptance recorded twice: no change
        return _out(session, q)
    status = engine.effective_status(q)
    if status == "expired":
        raise AppError(409, "QUOTATION_EXPIRED", "This quotation has expired; revise it before the customer can accept",
                       {"valid_until": q.valid_until.isoformat()})
    if q.status not in engine.OPEN_FOR_ACCEPTANCE:
        raise engine.invalid_state(q, "accept")
    if body.revision is not None and body.revision != q.revision:
        raise AppError(409, "REVISION_MISMATCH", f"The customer accepted revision {body.revision}, the current revision is {q.revision}")
    try:
        _claim(session, q, set(engine.OPEN_FOR_ACCEPTANCE), "accept")
    except AppError:
        if q.status == St.ACCEPTED and (body.revision is None or body.revision == q.accepted_revision):
            response.status_code = 200  # a concurrent duplicate of the same acceptance
            return _out(session, q)
        raise
    q.status, q.accepted_at, q.accepted_revision = St.ACCEPTED, utcnow(), q.revision
    q.acceptance = {"accepted_by_name": body.accepted_by_name, "method": body.method, "reference": body.reference,
                    "note": body.note, "evidence_url": body.evidence_url, "recorded_by": principal.actor}
    audit.record(session, org_id, "quotation", q.id, "quotation.accepted", principal.actor, {"revision": q.revision, **q.acceptance})
    events.emit(session, org_id, "quotation.accepted", f"quotation.accepted:{q.id}",
                events.quotation_data(session, q, accepted_by_name=body.accepted_by_name, method=body.method))
    session.commit()
    return _out(session, q)


@router.post("/quotations/{qid}/decline", response_model=s.QuotationOut)
def decline_quotation(qid: uuid.UUID, body: s.DeclineIn, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if q.status not in engine.OPEN_FOR_ACCEPTANCE:
        raise engine.invalid_state(q, "decline")
    _claim(session, q, set(engine.OPEN_FOR_ACCEPTANCE), "decline")
    q.status = St.DECLINED
    audit.record(session, org_id, "quotation", q.id, "quotation.declined", principal.actor, {"revision": q.revision, "reason": body.reason})
    session.commit()
    return _out(session, q)


@router.post("/quotations/{qid}/revise", response_model=s.QuotationOut)
def revise_quotation(qid: uuid.UUID, body: s.ReviseIn, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    """Start a new draft revision from a submitted one. The submitted revision stays frozen in its snapshot."""
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if q.status not in {St.PENDING_APPROVAL, St.APPROVED, St.SENT, St.DECLINED}:
        raise engine.invalid_state(q, "revise")
    _claim(session, q, {St.PENDING_APPROVAL, St.APPROVED, St.SENT, St.DECLINED}, "revise")
    previous = q.revision
    q.revision += 1
    q.status = St.DRAFT
    q.submitted_by = q.approved_by = q.approved_at = q.sent_at = q.sent_by = q.sent_method = None
    if q.valid_until < date.today():
        q.valid_until = engine.default_valid_until(session, org_id, date.today())
    audit.record(session, org_id, "quotation", q.id, "quotation.revised", principal.actor,
                 {"from_revision": previous, "to_revision": q.revision, "reason": body.reason})
    session.commit()
    return _out(session, q)


@router.post("/quotations/{qid}/cancel", response_model=s.QuotationOut)
def cancel_quotation(qid: uuid.UUID, body: s.DeclineIn, principal: Principal = Depends(require_user), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid, lock=True)
    if q.status in {St.ACCEPTED, St.CANCELLED}:
        raise engine.invalid_state(q, "cancel")
    _claim(session, q, set(St.ALL) - {St.ACCEPTED, St.CANCELLED}, "cancel")
    q.status = St.CANCELLED
    audit.record(session, org_id, "quotation", q.id, "quotation.cancelled", principal.actor, {"revision": q.revision, "reason": body.reason})
    session.commit()
    return _out(session, q)


# ---- history and documents -----------------------------------------------------------------------
@router.get("/quotations/{qid}/revisions", response_model=list[s.RevisionOut])
def list_revisions(qid: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    q = _quotation(session, principal.organization.id, qid)
    rows = session.scalars(select(QuotationRevision).where(QuotationRevision.quotation_id == q.id).order_by(QuotationRevision.revision)).all()
    return [s.RevisionOut(revision=r.revision, grand_total=r.grand_total, reason=r.reason, created_by=r.created_by, created_at=r.created_at)
            for r in rows]


@router.get("/quotations/{qid}/approvals", response_model=list[s.ApprovalOut])
def list_approvals(qid: uuid.UUID, principal: Principal = Depends(get_principal), session: Session = Depends(get_db)):
    q = _quotation(session, principal.organization.id, qid)
    rows = session.scalars(select(QuotationApproval).where(QuotationApproval.quotation_id == q.id).order_by(QuotationApproval.decided_at)).all()
    return [s.ApprovalOut(revision=r.revision, decision=r.decision, comment=r.comment, decided_by=r.decided_by, decided_at=r.decided_at)
            for r in rows]


@router.get("/quotations/{qid}/document")
def quotation_document(
    qid: uuid.UUID,
    format: str = Query(default="pdf", pattern="^(pdf|docx)$"),
    revision: int | None = Query(default=None, ge=1),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_db),
):
    """The quotation as Thai PDF or DOCX. Approved revisions print clean; anything else is watermarked as a
    draft and only for signed-in users (integration keys never receive unapproved documents)."""
    org_id = principal.organization.id
    q = _quotation(session, org_id, qid)
    rev_no = revision or q.revision
    rev = session.scalar(select(QuotationRevision).where(QuotationRevision.quotation_id == q.id, QuotationRevision.revision == rev_no))
    approved_revs = set(session.scalars(select(QuotationApproval.revision).where(QuotationApproval.quotation_id == q.id,
                                                                                 QuotationApproval.decision == "approved")))
    is_approved = rev is not None and rev_no in approved_revs and not (rev_no == q.revision and q.status in {St.DRAFT, St.PENDING_APPROVAL})
    if not is_approved and principal.user is None:
        raise AppError(403, "NOT_APPROVED", "Only approved quotations are available to integrations")
    if rev is not None:
        snap = rev.snapshot
    elif rev_no == q.revision:
        customer = session.get(Customer, q.customer_id)
        assert customer is not None
        snap = engine.snapshot(q, engine.lines_of(session, q), customer)
    else:
        raise not_found("Revision")
    settings = engine.settings_for(session, org_id)
    seller = {"name": principal.organization.name, "legal_name": settings.legal_name, "tax_id": settings.tax_id,
              "branch": settings.branch, "address": settings.address, "phone": settings.phone, "email": settings.email}
    if format == "pdf":
        data, media = docs.render_pdf(snap, seller, draft=not is_approved), "application/pdf"
    else:
        data, media = docs.render_docx(snap, seller, draft=not is_approved), \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    suffix = "" if is_approved else "-DRAFT"
    filename = f"{snap['number']}-r{rev_no}{suffix}.{format}"
    audit.record(session, org_id, "quotation", q.id, "quotation.document_downloaded", principal.actor,
                 {"revision": rev_no, "format": format, "approved": is_approved})
    session.commit()
    return RawResponse(content=data, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"})
