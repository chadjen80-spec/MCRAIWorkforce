"""/v1/cafe: staff and administrator endpoints. People only: integration keys and device tokens are refused here.

Roles: owner/admin = café administrator (enrol PCs, edit the app catalogue); member = staff (sessions, launch, help).
"""
from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cafe import flags, service
from app.cafe.device_auth import hash_secret
from app.cafe.models import CafeApp, CafeAssistRequest, CafeAuditEvent, CafeCommand, CafeDevice, CafeEnrollmentCode, CafeSeat, CafeSession
from app.config import get_settings
from app.db import get_db, utcnow
from app.deps import Principal, get_principal
from app.errors import conflict, forbidden, not_found

router = APIRouter(prefix="/v1/cafe", tags=["smart-cafe"])


def staff(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.user is None:
        raise forbidden("Café operations need a signed-in staff member")
    return principal


def admin(principal: Principal = Depends(staff)) -> Principal:
    if not principal.is_admin:
        raise forbidden("Café administrator (owner or admin) role required")
    return principal


@router.get("/features")
def get_features(_: Principal = Depends(get_principal)):
    return flags.features()


# ---- seats and devices ---------------------------------------------------------------------------------------
class SeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9\-]+$")
    zone: str = Field(default="", max_length=64)


def _seat_out(session: Session, seat: CafeSeat, assists: dict[uuid.UUID, int]) -> dict:
    device = service.seat_device(session, seat)
    cs = service.active_session(session, seat)
    return {
        "id": str(seat.id), "label": seat.label, "zone": seat.zone, "status": seat.status,
        "device": None if device is None else {
            "id": str(device.id), "name": device.name, "online": service.is_online(device), "client_version": device.client_version,
            "os_version": device.os_version, "last_heartbeat_at": device.last_heartbeat_at.isoformat() if device.last_heartbeat_at else None,
            "installed_apps": device.installed_apps or {},
        },
        "session": None if cs is None else {"id": str(cs.id), "started_at": cs.started_at.isoformat(), "granted_seconds": cs.granted_seconds,
                                            "remaining_seconds": service.remaining_seconds(cs)},
        "open_assists": assists.get(seat.id, 0),
    }


@router.get("/seats", dependencies=[Depends(flags.module_dep("devices"))])
def list_seats(principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    seats = session.scalars(select(CafeSeat).where(CafeSeat.organization_id == org_id).order_by(CafeSeat.label)).all()
    assists: dict[uuid.UUID, int] = {}
    for a in session.scalars(select(CafeAssistRequest).where(CafeAssistRequest.organization_id == org_id, CafeAssistRequest.status == "open")):
        assists[a.seat_id] = assists.get(a.seat_id, 0) + 1
    return {"items": [_seat_out(session, s, assists) for s in seats]}


@router.post("/seats", status_code=201, dependencies=[Depends(flags.module_dep("devices"))])
def create_seat(body: SeatIn, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    seat = CafeSeat(organization_id=principal.organization.id, label=body.label, zone=body.zone)
    session.add(seat)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict(f"Seat {body.label} already exists") from exc
    service.audit(session, seat.organization_id, "seat", seat.id, "seat.created", principal.actor, {"label": seat.label})
    session.commit()
    return _seat_out(session, seat, {})


@router.post("/seats/{seat_id}/enrollment-code", status_code=201, dependencies=[Depends(flags.module_dep("devices"))])
def create_enrollment_code(seat_id: uuid.UUID, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    """A one-time code for registering this seat's PC. Shown once; only its hash is stored."""
    seat = service.find_seat(session, principal.organization.id, seat_id=seat_id)
    code = "-".join(secrets.token_hex(2).upper() for _ in range(3))  # e.g. 3F2A-91BC-07DE
    expires = utcnow() + timedelta(minutes=get_settings().cafe_enrollment_code_minutes)
    row = CafeEnrollmentCode(organization_id=seat.organization_id, seat_id=seat.id, code_hash=hash_secret(code),
                             expires_at=expires, created_by=principal.actor)
    session.add(row)
    session.flush()
    service.audit(session, seat.organization_id, "seat", seat.id, "enrollment_code.created", principal.actor, {"expires_at": expires.isoformat()})
    session.commit()
    return {"code": code, "seat_label": seat.label, "expires_at": expires.isoformat()}


@router.post("/devices/{device_id}/revoke", dependencies=[Depends(flags.module_dep("devices"))])
def revoke_device(device_id: uuid.UUID, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    device = session.get(CafeDevice, device_id)
    if device is None or device.organization_id != principal.organization.id:
        raise not_found("Device")
    if device.status != "revoked":
        device.status = "revoked"
        device.revoked_at = utcnow()
        service.audit(session, device.organization_id, "device", device.id, "device.revoked", principal.actor, {})
        session.commit()
    return {"id": str(device.id), "status": device.status}


# ---- app catalogue -----------------------------------------------------------------------------------------------
class AppIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
    name: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    category: Literal["game", "launcher", "browser", "office", "tool"] = "game"
    enabled: bool = True
    requires_session: bool = True


def _app_out(a: CafeApp) -> dict:
    return {"id": str(a.id), "app_key": a.app_key, "name": a.name, "aliases": a.aliases or [], "category": a.category,
            "enabled": a.enabled, "requires_session": a.requires_session}


@router.get("/apps", dependencies=[Depends(flags.module_dep("launcher"))])
def list_apps(principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    rows = session.scalars(select(CafeApp).where(CafeApp.organization_id == principal.organization.id).order_by(CafeApp.name)).all()
    return {"items": [_app_out(a) for a in rows]}


@router.post("/apps", status_code=201, dependencies=[Depends(flags.module_dep("launcher"))])
def create_app(body: AppIn, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    app = CafeApp(organization_id=principal.organization.id, app_key=body.app_key, name=body.name,
                  aliases=[a.strip()[:64] for a in body.aliases if a.strip()], category=body.category, enabled=body.enabled,
                  requires_session=body.requires_session)
    session.add(app)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise conflict(f"App {body.app_key} already exists") from exc
    service.audit(session, app.organization_id, "app", app.id, "app.created", principal.actor, _app_out(app))
    session.commit()
    return _app_out(app)


class AppPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    requires_session: bool | None = None


@router.patch("/apps/{app_id}", dependencies=[Depends(flags.module_dep("launcher"))])
def patch_app(app_id: uuid.UUID, body: AppPatch, principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    app = session.get(CafeApp, app_id)
    if app is None or app.organization_id != principal.organization.id:
        raise not_found("App")
    patch = body.model_dump(exclude_none=True)
    for k, v in patch.items():
        setattr(app, k, v)
    service.audit(session, app.organization_id, "app", app.id, "app.updated", principal.actor, patch)
    session.commit()
    return _app_out(app)


# ---- sessions ------------------------------------------------------------------------------------------------------
class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int = Field(ge=1, le=24 * 60)
    customer_ref: str = Field(default="guest", max_length=64)


@router.post("/seats/{seat_id}/sessions", status_code=201, dependencies=[Depends(flags.module_dep("sessions"))])
def start_session(seat_id: uuid.UUID, body: SessionIn, principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    """Staff open a session with granted minutes (paid at the counter; POS and packages are a later module)."""
    seat = service.find_seat(session, principal.organization.id, seat_id=seat_id)
    current = service.active_session(session, seat)
    if current is not None:
        if service.remaining_seconds(current) > 0:
            raise conflict(f"Seat {seat.label} already has an active session")
        current.status, current.ended_at = "ended", utcnow()
        service.audit(session, seat.organization_id, "session", current.id, "session.time_up", principal.actor, {})
    device = service.seat_device(session, seat)
    cs = CafeSession(organization_id=seat.organization_id, seat_id=seat.id, device_id=device.id if device else None,
                     granted_seconds=body.minutes * 60, customer_ref=body.customer_ref, started_by=principal.actor)
    session.add(cs)
    session.flush()
    service.audit(session, seat.organization_id, "session", cs.id, "session.started", principal.actor,
                  {"seat": seat.label, "minutes": body.minutes, "customer_ref": body.customer_ref})
    session.commit()
    return {"id": str(cs.id), "seat_label": seat.label, "remaining_seconds": service.remaining_seconds(cs)}


@router.post("/sessions/{session_id}/end", dependencies=[Depends(flags.module_dep("sessions"))])
def end_session(session_id: uuid.UUID, principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    cs = session.get(CafeSession, session_id)
    if cs is None or cs.organization_id != principal.organization.id:
        raise not_found("Session")
    if cs.status == "active":
        cs.status, cs.ended_at = "ended", utcnow()
        service.audit(session, cs.organization_id, "session", cs.id, "session.ended", principal.actor, {})
        session.commit()
    return {"id": str(cs.id), "status": cs.status}


@router.get("/sessions", dependencies=[Depends(flags.module_dep("sessions"))])
def list_sessions(status: Literal["active", "ended", "all"] = "active", principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    org_id = principal.organization.id
    q = select(CafeSession, CafeSeat.label).join(CafeSeat, CafeSeat.id == CafeSession.seat_id).where(CafeSession.organization_id == org_id)
    if status != "all":
        q = q.where(CafeSession.status == status)
    rows = session.execute(q.order_by(CafeSession.started_at.desc()).limit(200)).all()
    return {"items": [{"id": str(cs.id), "seat_label": label, "status": cs.status, "granted_seconds": cs.granted_seconds,
                       "remaining_seconds": service.remaining_seconds(cs), "started_at": cs.started_at.isoformat(),
                       "ended_at": cs.ended_at.isoformat() if cs.ended_at else None, "customer_ref": cs.customer_ref} for cs, label in rows]}


# ---- launch and commands --------------------------------------------------------------------------------------------
class LaunchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_key: str = Field(min_length=1, max_length=64)


@router.post("/seats/{seat_id}/launch", status_code=202, dependencies=[Depends(flags.module_dep("launcher"))])
def launch(seat_id: uuid.UUID, body: LaunchIn, principal: Principal = Depends(staff), session: Session = Depends(get_db),
           idempotency_key: str | None = Header(default=None, max_length=128)):
    seat = service.find_seat(session, principal.organization.id, seat_id=seat_id)
    cmd = service.launch(session, seat, body.app_key, actor=principal.actor, via="ui", idempotency_key=idempotency_key)
    return service.command_out(cmd, seat.label)


@router.get("/commands", dependencies=[Depends(flags.module_dep("launcher"))])
def list_commands(limit: int = Query(default=20, ge=1, le=100), principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    rows = session.execute(select(CafeCommand, CafeSeat.label).join(CafeSeat, CafeSeat.id == CafeCommand.seat_id)
                           .where(CafeCommand.organization_id == principal.organization.id)
                           .order_by(CafeCommand.created_at.desc()).limit(limit)).all()
    return {"items": [service.command_out(service.expire_stale(session, c), label) for c, label in rows]}


@router.get("/commands/{command_id}", dependencies=[Depends(flags.module_dep("launcher"))])
def get_command(command_id: uuid.UUID, principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    cmd = session.get(CafeCommand, command_id)
    if cmd is None or cmd.organization_id != principal.organization.id:
        raise not_found("Command")
    seat = session.get(CafeSeat, cmd.seat_id)
    return service.command_out(service.expire_stale(session, cmd), seat.label if seat else None)


# ---- staff assistance -------------------------------------------------------------------------------------------------
@router.get("/assist", dependencies=[Depends(flags.module_dep("assist"))])
def list_assist(status: Literal["open", "resolved", "all"] = "open", principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    q = (select(CafeAssistRequest, CafeSeat.label).join(CafeSeat, CafeSeat.id == CafeAssistRequest.seat_id)
         .where(CafeAssistRequest.organization_id == principal.organization.id))
    if status != "all":
        q = q.where(CafeAssistRequest.status == status)
    rows = session.execute(q.order_by(CafeAssistRequest.created_at.desc()).limit(100)).all()
    return {"items": [{"id": str(a.id), "seat_label": label, "message": a.message, "source": a.source, "status": a.status,
                       "created_at": a.created_at.isoformat()} for a, label in rows]}


@router.post("/assist/{assist_id}/resolve", dependencies=[Depends(flags.module_dep("assist"))])
def resolve_assist(assist_id: uuid.UUID, principal: Principal = Depends(staff), session: Session = Depends(get_db)):
    a = session.get(CafeAssistRequest, assist_id)
    if a is None or a.organization_id != principal.organization.id:
        raise not_found("Help request")
    if a.status == "open":
        a.status, a.resolved_by, a.resolved_at = "resolved", principal.actor, utcnow()
        service.audit(session, a.organization_id, "assist", a.id, "assist.resolved", principal.actor, {})
        session.commit()
    return {"id": str(a.id), "status": a.status}


@router.get("/audit", dependencies=[Depends(flags.module_dep("devices"))])
def list_audit(limit: int = Query(default=50, ge=1, le=200), principal: Principal = Depends(admin), session: Session = Depends(get_db)):
    rows = session.scalars(select(CafeAuditEvent).where(CafeAuditEvent.organization_id == principal.organization.id)
                           .order_by(CafeAuditEvent.created_at.desc()).limit(limit)).all()
    return {"items": [{"entity_type": e.entity_type, "entity_id": str(e.entity_id), "action": e.action, "actor": e.actor,
                       "detail": e.detail, "created_at": e.created_at.isoformat()} for e in rows]}
