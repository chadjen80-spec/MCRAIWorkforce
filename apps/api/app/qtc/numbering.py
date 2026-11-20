"""Gap-tolerant, duplicate-free numbering for customer codes and commercial documents.

`take(session, org_id, kind)` increments the (organization, kind) counter with a single
`UPDATE … SET next_value = next_value + 1 … RETURNING`, which is atomic on PostgreSQL (row lock, held
until the caller's transaction ends) and on SQLite (database write lock). Two concurrent requests can
therefore never receive the same number. If the caller's transaction rolls back, the increment rolls back
with it. Unique constraints on the numbered tables remain the second line of defence.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.qtc.models import NumberSequence


def _increment(session: Session, org_id: uuid.UUID, kind: str) -> int | None:
    stmt = (
        update(NumberSequence)
        .where(NumberSequence.organization_id == org_id, NumberSequence.kind == kind)
        .values(next_value=NumberSequence.next_value + 1)
        .returning(NumberSequence.next_value)
    )
    new = session.execute(stmt).scalar_one_or_none()
    return None if new is None else int(new) - 1


def take(session: Session, org_id: uuid.UUID, kind: str, *, start: Callable[[], int] = lambda: 1) -> int:
    """Next number for `kind`. `start()` gives the first value when the counter does not exist yet."""
    value = _increment(session, org_id, kind)
    if value is not None:
        return value
    try:
        with session.begin_nested():
            session.add(NumberSequence(organization_id=org_id, kind=kind, next_value=max(1, start())))
            session.flush()
    except IntegrityError:
        pass  # another request created the counter first
    value = _increment(session, org_id, kind)
    assert value is not None
    return value
