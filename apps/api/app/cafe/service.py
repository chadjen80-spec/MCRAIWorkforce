"""Café rules shared by the staff API, the device API/WebSocket and Mira. One place decides who may launch what."""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cafe import signing
from app.cafe.models import CafeApp, CafeAssistRequest, CafeAuditEvent, CafeCommand, CafeDevice, CafeSeat, CafeSession
from app.config import get_settings
from app.db import utcnow
from app.errors import AppError

COMMAND_TYPES = ("launch_app", "ping")
RESULT_CODES = {
    "STARTED", "NOT_ALLOWLISTED", "NOT_INSTALLED", "START_FAILED", "SIGNATURE_INVALID", "WRONG_DEVICE", "EXPIRED",
    "REPLAYED", "UNKNOWN_TYPE", "BAD_ARGS", "PONG", "NEEDS_ELEVATION",
}


class CafeError(AppError):
    def __init__(self, code: str, message: str, status: int = 409, details: dict[str, Any] | None = None):
        super().__init__(status, code, message, details)


def audit(session: Session, org_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID, action: str, actor: str,
          detail: dict[str, Any] | None = None) -> None:
    session.add(CafeAuditEvent(organization_id=org_id, entity_type=entity_type, entity_id=entity_id, action=action,
                               actor=actor, detail=detail or {}))


# ---- reads ------------------------------------------------------------------------------------------------
def is_online(device: CafeDevice | None, now: datetime | None = None) -> bool:
    if device is None or device.status != "active" or device.last_heartbeat_at is None:
        return False
    now = now or utcnow()
    return (now - device.last_heartbeat_at).total_seconds() <= get_settings().cafe_device_online_seconds


def seat_device(session: Session, seat: CafeSeat) -> CafeDevice | None:
    return session.scalar(select(CafeDevice).where(CafeDevice.organization_id == seat.organization_id, CafeDevice.seat_id == seat.id,
                                                   CafeDevice.status == "active").order_by(CafeDevice.created_at.desc()))


def active_session(session: Session, seat: CafeSeat) -> CafeSession | None:
    return session.scalar(select(CafeSession).where(CafeSession.organization_id == seat.organization_id, CafeSession.seat_id == seat.id,
                                                    CafeSession.status == "active"))


def remaining_seconds(cs: CafeSession | None, now: datetime | None = None) -> int:
    if cs is None or cs.status != "active":
        return 0
    used = ((now or utcnow()) - cs.started_at).total_seconds()
    return max(0, int(cs.granted_seconds - used))


def find_seat(session: Session, org_id: uuid.UUID, *, seat_id: uuid.UUID | None = None, label: str | None = None) -> CafeSeat:
    q = select(CafeSeat).where(CafeSeat.organization_id == org_id)
    if seat_id is not None:
        q = q.where(CafeSeat.id == seat_id)
    elif label is not None:
        q = q.where(CafeSeat.label == label.strip())
    else:
        raise CafeError("SEAT_REQUIRED", "Which seat?", 422)
    seat = session.scalar(q)
    if seat is None:
        raise CafeError("SEAT_NOT_FOUND", "Seat not found", 404, {"seat": label or str(seat_id)})
    return seat


def find_app(session: Session, org_id: uuid.UUID, app_key: str) -> CafeApp | None:
    return session.scalar(select(CafeApp).where(CafeApp.organization_id == org_id, CafeApp.app_key == app_key))


# ---- launch -----------------------------------------------------------------------------------------------
def authorize_launch(session: Session, seat: CafeSeat, app_key: str) -> tuple[CafeDevice, CafeApp]:
    """Every check the server makes before it signs a launch. Raises CafeError with a code Mira can explain."""
    if seat.status != "active":
        raise CafeError("SEAT_UNAVAILABLE", f"Seat {seat.label} is not in service", details={"seat": seat.label})
    app = find_app(session, seat.organization_id, app_key)
    if app is None:
        raise CafeError("APP_NOT_IN_CATALOG", f"'{app_key}' is not an approved application", 404, {"app_key": app_key})
    if not app.enabled:
        raise CafeError("APP_DISABLED", f"{app.name} is disabled in the catalogue", details={"app": app.name})
    device = seat_device(session, seat)
    if device is None:
        raise CafeError("NO_DEVICE", f"Seat {seat.label} has no registered PC", details={"seat": seat.label})
    if not is_online(device):
        raise CafeError("DEVICE_OFFLINE", f"The PC at seat {seat.label} is offline", details={"seat": seat.label})
    if (device.installed_apps or {}).get(app.app_key) != "installed":
        raise CafeError("APP_NOT_INSTALLED", f"{app.name} is not installed on seat {seat.label}",
                        details={"seat": seat.label, "app": app.name})
    if app.requires_session and remaining_seconds(active_session(session, seat)) <= 0:
        raise CafeError("NO_ACTIVE_SESSION", f"Seat {seat.label} has no active session with time left", details={"seat": seat.label})
    return device, app


def dispatch(session: Session, seat: CafeSeat, device: CafeDevice, ctype: str, args: dict[str, Any], *, actor: str,
             via: str = "ui", idempotency_key: str | None = None) -> CafeCommand:
    if ctype not in COMMAND_TYPES:
        raise CafeError("UNKNOWN_COMMAND", "Unknown command type", 422)
    if idempotency_key:
        existing = session.scalar(select(CafeCommand).where(CafeCommand.organization_id == seat.organization_id,
                                                            CafeCommand.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    now = utcnow()
    ttl = get_settings().cafe_command_ttl_seconds
    command_id = uuid.uuid4()
    envelope = {
        "v": 1, "command_id": str(command_id), "org_id": str(seat.organization_id), "device_id": str(device.id),
        "seat_label": seat.label, "type": ctype, "args": args, "issued_at": int(now.timestamp()),
        "expires_at": int(now.timestamp()) + ttl, "nonce": secrets.token_urlsafe(12),
    }
    text, sig = signing.sign(envelope)
    cmd = CafeCommand(id=command_id, organization_id=seat.organization_id, device_id=device.id, seat_id=seat.id, type=ctype,
                      args=args, envelope=text, signature=sig, status="queued", idempotency_key=idempotency_key or None,
                      requested_by=actor, via=via, expires_at=now + timedelta(seconds=ttl))
    session.add(cmd)
    audit(session, seat.organization_id, "command", command_id, "command.queued", actor,
          {"type": ctype, "args": args, "seat": seat.label, "device_id": str(device.id), "via": via})
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(CafeCommand).where(CafeCommand.organization_id == seat.organization_id,
                                                            CafeCommand.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    return cmd


def launch(session: Session, seat: CafeSeat, app_key: str, *, actor: str, via: str = "ui",
           idempotency_key: str | None = None) -> CafeCommand:
    if idempotency_key:  # a retry returns the first command even if the seat changed since
        existing = session.scalar(select(CafeCommand).where(CafeCommand.organization_id == seat.organization_id,
                                                            CafeCommand.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
    device, app = authorize_launch(session, seat, app_key)
    return dispatch(session, seat, device, "launch_app", {"app_id": app.app_key}, actor=actor, via=via, idempotency_key=idempotency_key)


def expire_stale(session: Session, cmd: CafeCommand) -> CafeCommand:
    """A command nobody answered within its life (plus grace for a slow result) is reported as expired, not as running."""
    if cmd.status in ("queued", "delivered") and utcnow() > cmd.expires_at + timedelta(seconds=30):
        cmd.status = "expired"
        cmd.completed_at = utcnow()
        cmd.result = {"code": "EXPIRED", "message": "No result from the seat PC before the command expired"}
        audit(session, cmd.organization_id, "command", cmd.id, "command.expired", "system", {})
        session.commit()
    return cmd


def pending_for_device(session: Session, device: CafeDevice, limit: int = 20) -> list[CafeCommand]:
    now = utcnow()
    rows = session.scalars(select(CafeCommand).where(CafeCommand.organization_id == device.organization_id, CafeCommand.device_id == device.id,
                                                     CafeCommand.status == "queued").order_by(CafeCommand.created_at).limit(limit)).all()
    out = []
    for cmd in rows:
        if cmd.expires_at < now:
            cmd.status = "expired"
            cmd.completed_at = now
            cmd.result = {"code": "EXPIRED", "message": "Expired before the seat PC picked it up"}
            audit(session, cmd.organization_id, "command", cmd.id, "command.expired", "system", {})
            continue
        cmd.status = "delivered"
        cmd.delivered_at = now
        out.append(cmd)
    session.commit()
    return out


def wire(cmd: CafeCommand) -> dict[str, Any]:
    return {"type": "command", "command_id": str(cmd.id), "envelope": cmd.envelope, "signature": cmd.signature}


def record_result(session: Session, device: CafeDevice, command_id: uuid.UUID, ok: bool, code: str, message: str = "",
                  detail: dict[str, Any] | None = None) -> CafeCommand:
    cmd = session.get(CafeCommand, command_id)
    # device-scoped: a seat PC can only answer its own commands
    if cmd is None or cmd.organization_id != device.organization_id or cmd.device_id != device.id:
        raise CafeError("COMMAND_NOT_FOUND", "Command not found", 404)
    status = "succeeded" if ok else "failed"
    if cmd.status in ("succeeded", "failed", "expired"):
        if cmd.status == status and (cmd.result or {}).get("code") == code:
            return cmd  # a retried report of the same result
        raise CafeError("COMMAND_COMPLETED", f"Command already {cmd.status}")
    if code not in RESULT_CODES:
        code = "START_FAILED" if not ok else "STARTED"
    clean = {k: v for k, v in (detail or {}).items() if k in ("pid", "app_id", "elapsed_ms") and isinstance(v, int | str)}
    cmd.status = status
    cmd.completed_at = utcnow()
    cmd.result = {"code": code, "message": message[:300], **clean}
    audit(session, cmd.organization_id, "command", cmd.id, f"command.{status}", f"device:{device.id}", cmd.result)
    session.commit()
    return cmd


def heartbeat(session: Session, device: CafeDevice, *, client_version: str = "", os_version: str = "",
              installed: dict[str, Any] | None = None, ip: str = "") -> None:
    device.last_heartbeat_at = utcnow()
    device.client_version = client_version[:32]
    device.os_version = os_version[:128]
    if installed is not None:
        device.installed_apps = {str(k)[:64]: ("installed" if v == "installed" else "missing") for k, v in list(installed.items())[:500]}
    if ip:
        device.last_ip = ip[:64]
    session.commit()


def request_assist(session: Session, seat: CafeSeat, device: CafeDevice | None, message: str, *, source: str, actor: str) -> CafeAssistRequest:
    req = CafeAssistRequest(organization_id=seat.organization_id, seat_id=seat.id, device_id=device.id if device else None,
                            message=message[:500], source=source, created_by=actor)
    session.add(req)
    session.flush()
    audit(session, seat.organization_id, "assist", req.id, "assist.requested", actor, {"seat": seat.label, "source": source})
    session.commit()
    return req


def command_out(cmd: CafeCommand, seat_label: str | None = None) -> dict[str, Any]:
    return {"id": str(cmd.id), "type": cmd.type, "args": cmd.args, "status": cmd.status, "result": cmd.result,
            "seat_label": seat_label, "requested_by": cmd.requested_by, "via": cmd.via,
            "created_at": cmd.created_at.isoformat(), "completed_at": cmd.completed_at.isoformat() if cmd.completed_at else None}
