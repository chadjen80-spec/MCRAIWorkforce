"""Quote-to-Cash events for n8n: a transactional outbox delivered as signed webhooks.

Writing: `emit()` adds a `QtcEvent` in the caller's transaction, so an event exists exactly when the change it
reports was committed. Each event has a `dedupe_key`; emitting the same key twice is a no-op (safe under retries
and concurrent scans).

Delivery: `deliver_pending()` (Celery beat, every 15 s) POSTs each pending event to
`{QTC_EVENTS_URL}-{slug}` with

    X-MCR-Event: quotation.submitted
    X-MCR-Event-Id: <uuid>
    X-MCR-Timestamp: <unix seconds>
    X-MCR-Signature: sha256=<hex HMAC-SHA256(QTC_EVENTS_SECRET, f"{timestamp}.{body}")>

n8n checks the signature and the timestamp (5-minute window) before doing anything. Failed deliveries are retried
with exponential backoff, then marked failed. Nothing is delivered until both settings are set.

Events carry what an internal notification needs (numbers, names, amounts as strings, links) and never contact
details: n8n notifies the team, not customers.

Scanning: `scan()` (Celery beat, hourly) emits "invoice due soon", "invoice overdue" and the weekly receivables
summary. The dedupe keys make the hourly scan produce each event once.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import Organization
from app.qtc.models import Customer, Invoice, QtcEvent

log = logging.getLogger("mcr.qtc.events")

EVENT_TYPES = (
    "quotation.submitted",
    "quotation.decided",
    "quotation.accepted",
    "invoice.due_soon",
    "invoice.overdue",
    "receivables.weekly_summary",
)
MAX_ATTEMPTS = 8
TRANSPORT: httpx.BaseTransport | None = None  # tests replace this with httpx.MockTransport


def slug(event_type: str) -> str:
    return event_type.replace(".", "-").replace("_", "-")


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


def emit(session: Session, org_id: uuid.UUID, event_type: str, dedupe_key: str, data: dict[str, Any]) -> QtcEvent | None:
    """Record an event in the current transaction. Returns None when this dedupe key already exists."""
    assert event_type in EVENT_TYPES, event_type
    exists = session.scalar(select(QtcEvent.id).where(QtcEvent.organization_id == org_id, QtcEvent.dedupe_key == dedupe_key))
    if exists:
        return None
    event = QtcEvent(organization_id=org_id, event_type=event_type, dedupe_key=dedupe_key[:200], payload=_jsonable(data))
    try:
        with session.begin_nested():  # a concurrent emitter of the same key loses quietly, not the caller's transaction
            session.add(event)
    except IntegrityError:
        return None
    return event


def body_of(event: QtcEvent) -> str:
    """The exact bytes that are signed and sent. Compact JSON, UTF-8, keys in a fixed order."""
    envelope = {
        "id": str(event.id),
        "type": event.event_type,
        "organization_id": str(event.organization_id),
        "created_at": event.created_at.isoformat(),
        "data": event.payload,
    }
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))


def sign(secret: str, timestamp: int, body: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: int, body: str, signature: str, now: float | None = None, tolerance: int = 300) -> bool:
    """What a receiver does (n8n's Verify Signature node does the same in JavaScript)."""
    if abs((now if now is not None else time.time()) - timestamp) > tolerance:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


def delivery_configured() -> bool:
    s = get_settings()
    return bool(s.qtc_events_url) and len(s.qtc_events_secret) >= 32


def deliver_pending(session: Session, limit: int = 50) -> dict[str, int]:
    """Send due events. Safe to run in parallel: rows are locked and skipped by other workers (PostgreSQL)."""
    counts = {"delivered": 0, "retry": 0, "failed": 0}
    if not delivery_configured():
        return counts
    settings = get_settings()
    with httpx.Client(timeout=10, transport=TRANSPORT) as client:
        for _ in range(limit):
            # One event per transaction, locked while it is sent: parallel workers skip it (PostgreSQL), and a
            # crash never re-sends an event that was already marked delivered.
            event = session.scalars(
                select(QtcEvent)
                .where(QtcEvent.status == "pending", QtcEvent.next_attempt_at <= utcnow())
                .order_by(QtcEvent.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if event is None:
                break
            body = body_of(event)
            ts = int(time.time())
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "X-MCR-Event": event.event_type,
                "X-MCR-Event-Id": str(event.id),
                "X-MCR-Timestamp": str(ts),
                "X-MCR-Signature": sign(settings.qtc_events_secret, ts, body),
            }
            error: str | None = None
            try:
                r = client.post(f"{settings.qtc_events_url}-{slug(event.event_type)}", content=body.encode(), headers=headers)
                if not 200 <= r.status_code < 300:
                    error = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"[:300]
            event.attempts += 1
            if error is None:
                event.status, event.delivered_at, event.last_error = "delivered", utcnow(), None
                counts["delivered"] += 1
            elif event.attempts >= MAX_ATTEMPTS:
                event.status, event.last_error = "failed", error
                counts["failed"] += 1
                log.warning("qtc event %s (%s) failed after %s attempts: %s", event.id, event.event_type, event.attempts, error)
            else:
                event.last_error = error
                event.next_attempt_at = utcnow() + timedelta(seconds=min(30 * 2**event.attempts, 6 * 3600))
                counts["retry"] += 1
            session.commit()
    return counts


# ---- what the events contain --------------------------------------------------------------------------
def quotation_data(session: Session, q: Any, **extra: Any) -> dict[str, Any]:
    customer = session.get(Customer, q.customer_id)
    return {
        "quotation_id": q.id,
        "number": q.number,
        "revision": q.revision,
        "title": q.title,
        "customer": customer.name if customer else None,
        "grand_total": q.grand_total,
        "link": f"/qtc/quotations/{q.id}",
        **extra,
    }


def _invoice_data(inv: Invoice, customer: Customer | None, today: date) -> dict[str, Any]:
    return {
        "invoice_id": inv.id,
        "number": inv.number,
        "customer": customer.name if customer else None,
        "total": inv.total,
        "balance": inv.total - inv.paid_amount,
        "due_date": inv.due_date,
        "days_overdue": (today - inv.due_date).days if inv.due_date and inv.due_date < today else 0,
        "link": f"/qtc/invoices/{inv.id}",
    }


def scan(session: Session, today: date | None = None) -> dict[str, int]:
    """Emit due-soon, overdue and weekly-summary events for every organization. Idempotent."""
    from app.qtc.dashboard import summary

    today = today or date.today()
    counts = {"invoice.due_soon": 0, "invoice.overdue": 0, "receivables.weekly_summary": 0}
    soon = today + timedelta(days=get_settings().qtc_due_soon_days)
    invoices = session.scalars(
        select(Invoice).where(Invoice.status.in_(("issued", "partially_paid")), Invoice.due_date.is_not(None), Invoice.due_date <= soon)
    ).all()
    customers = {c.id: c for c in session.scalars(select(Customer).where(Customer.id.in_({i.customer_id for i in invoices})))} if invoices else {}
    for inv in invoices:
        assert inv.due_date is not None
        kind = "invoice.overdue" if inv.due_date < today else "invoice.due_soon"
        if emit(
            session,
            inv.organization_id,
            kind,
            f"{kind}:{inv.id}:{inv.due_date.isoformat()}",
            _invoice_data(inv, customers.get(inv.customer_id), today),
        ):
            counts[kind] += 1
    year, week, _ = today.isocalendar()
    for org in session.scalars(select(Organization)):
        figures = summary(session, org.id, today)
        if not (figures["counts"]["open_invoices"] or figures["counts"]["open_quotations"] or Decimal(figures["cash_received"]["amount"])):
            continue  # an organization that does not use Quote-to-Cash gets no weekly mail
        if emit(
            session,
            org.id,
            "receivables.weekly_summary",
            f"receivables.weekly_summary:{year}-W{week:02d}",
            {"week": f"{year}-W{week:02d}", **figures},
        ):
            counts["receivables.weekly_summary"] += 1
    session.commit()
    return counts
