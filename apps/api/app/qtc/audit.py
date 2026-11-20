"""QTC audit trail: one append-only row per commercial change, written in the same transaction."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.qtc.models import QtcAuditEvent


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


def record(session: Session, org_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID, action: str, actor: str,
           detail: dict[str, Any] | None = None) -> None:
    session.add(QtcAuditEvent(organization_id=org_id, entity_type=entity_type, entity_id=entity_id, action=action,
                              actor=actor, detail=_plain(detail or {})))


def changes(obj: Any, patch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Apply `patch` to `obj`; return {field: {"from": old, "to": new}} for the fields that changed."""
    diff: dict[str, dict[str, Any]] = {}
    for key, new in patch.items():
        old = getattr(obj, key)
        if old != new:
            diff[key] = {"from": _plain(old), "to": _plain(new)}
            setattr(obj, key, new)
    return diff
