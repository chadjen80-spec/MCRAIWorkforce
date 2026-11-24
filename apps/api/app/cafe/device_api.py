"""/v1/cafe/device: the seat PC's own API. Every endpoint is scoped to the device in the token and its seat."""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import time
import uuid
from typing import Any, Literal

import jwt
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.cafe import flags, service, signing
from app.cafe.device_auth import DeviceContext, get_device, hash_secret, issue_device_token, load_device, new_secret
from app.cafe.models import CafeDevice, CafeEnrollmentCode, CafeSeat
from app.config import get_settings
from app.db import get_db, new_session, utcnow
from app.deps import client_ip, rate_limited
from app.errors import AppError, unauthenticated

router = APIRouter(prefix="/v1/cafe/device", tags=["smart-cafe-device"], dependencies=[Depends(flags.module_dep("devices"))])
ws_router = APIRouter(prefix="/v1/cafe/device", tags=["smart-cafe-device"])


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_code: str = Field(min_length=6, max_length=32)
    name: str = Field(default="", max_length=128)
    client_version: str = Field(default="", max_length=32)
    os_version: str = Field(default="", max_length=128)


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, session: Session = Depends(get_db)):
    """Exchange a one-time enrolment code for a device identity. The secret is returned once and stored as a hash."""
    rate_limited(f"cafe-register:{client_ip(request)}", 10)
    code = session.scalar(select(CafeEnrollmentCode).where(CafeEnrollmentCode.code_hash == hash_secret(body.enrollment_code.strip().upper())))
    if code is None or code.used_at is not None or code.expires_at < utcnow():
        raise unauthenticated("Enrolment code is invalid, used or expired")
    seat = session.get(CafeSeat, code.seat_id)
    assert seat is not None and seat.organization_id == code.organization_id
    # one active PC per seat: a re-enrolment replaces (revokes) the previous one
    for old in session.scalars(select(CafeDevice).where(CafeDevice.organization_id == seat.organization_id, CafeDevice.seat_id == seat.id,
                                                        CafeDevice.status == "active")):
        old.status, old.revoked_at = "revoked", utcnow()
        service.audit(session, seat.organization_id, "device", old.id, "device.replaced", "enrolment", {})
    secret, digest = new_secret("mcd_")
    device = CafeDevice(organization_id=seat.organization_id, seat_id=seat.id, name=body.name or f"Seat {seat.label}", secret_hash=digest,
                        client_version=body.client_version, os_version=body.os_version, enrolled_by=code.created_by)
    session.add(device)
    code.used_at = utcnow()
    session.flush()
    service.audit(session, seat.organization_id, "device", device.id, "device.registered", f"device:{device.id}",
                  {"seat": seat.label, "name": device.name, "client_version": body.client_version})
    session.commit()
    return {"device_id": str(device.id), "device_secret": secret, "organization_id": str(seat.organization_id),
            "seat_label": seat.label, "server_public_key": signing.public_key_pem()}


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: uuid.UUID
    device_secret: str = Field(min_length=10, max_length=200)


@router.post("/token")
def token(body: TokenIn, request: Request, session: Session = Depends(get_db)):
    rate_limited(f"cafe-token:{client_ip(request)}", 30)
    device = session.get(CafeDevice, body.device_id)
    if device is None or device.status != "active" or not hmac.compare_digest(device.secret_hash, hash_secret(body.device_secret)):
        raise unauthenticated("Unknown or revoked device")
    tok, expires = issue_device_token(device)
    return {"access_token": tok, "token_type": "bearer", "expires_in": expires}


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_version: str = Field(default="", max_length=32)
    os_version: str = Field(default="", max_length=128)
    installed_apps: dict[str, Literal["installed", "missing"]] | None = None


def _status(session: Session, ctx: DeviceContext) -> dict[str, Any]:
    cs = service.active_session(session, ctx.seat)
    return {"device_id": str(ctx.device.id), "seat_label": ctx.seat.label, "session_active": cs is not None and service.remaining_seconds(cs) > 0,
            "remaining_seconds": service.remaining_seconds(cs), "server_time": int(time.time())}


@router.post("/heartbeat")
def post_heartbeat(body: HeartbeatIn, request: Request, ctx: DeviceContext = Depends(get_device), session: Session = Depends(get_db)):
    service.heartbeat(session, ctx.device, client_version=body.client_version, os_version=body.os_version,
                      installed=body.installed_apps, ip=client_ip(request))
    return _status(session, ctx)


@router.get("/status")
def get_status(ctx: DeviceContext = Depends(get_device), session: Session = Depends(get_db)):
    return _status(session, ctx)


@router.get("/commands")
def get_commands(ctx: DeviceContext = Depends(get_device), session: Session = Depends(get_db)):
    return {"items": [service.wire(c) for c in service.pending_for_device(session, ctx.device)]}


class ResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    code: str = Field(max_length=32)
    message: str = Field(default="", max_length=300)
    detail: dict[str, Any] = Field(default_factory=dict)


@router.post("/commands/{command_id}/result")
def post_result(command_id: uuid.UUID, body: ResultIn, ctx: DeviceContext = Depends(get_device), session: Session = Depends(get_db)):
    cmd = service.record_result(session, ctx.device, command_id, body.ok, body.code, body.message, body.detail)
    return {"id": str(cmd.id), "status": cmd.status}


class AssistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(default="", max_length=500)


@router.post("/assist", status_code=201)
def post_assist(body: AssistIn, ctx: DeviceContext = Depends(get_device), session: Session = Depends(get_db)):
    flags.require("assist")
    req = service.request_assist(session, ctx.seat, ctx.device, body.message, source="seat", actor=ctx.actor)
    return {"id": str(req.id), "status": req.status}


# ---- WebSocket --------------------------------------------------------------------------------------------------
def _ws_auth(token: str) -> tuple[uuid.UUID, int]:
    """Returns (device id, token expiry). The socket is closed when the token expires; the client reconnects."""
    with new_session() as session:
        ctx = load_device(session, token)  # verifies signature, audience, expiry and that the device is active
        exp = int(jwt.decode(token, options={"verify_signature": False})["exp"])
        return ctx.device.id, exp


def _ws_pending(device_id: uuid.UUID) -> list[dict[str, Any]] | None:
    with new_session() as session:
        device = session.get(CafeDevice, device_id)
        if device is None or device.status != "active":
            return None
        return [service.wire(c) for c in service.pending_for_device(session, device)]


def _ws_message(device_id: uuid.UUID, msg: dict[str, Any], ip: str) -> dict[str, Any]:
    with new_session() as session:
        device = session.get(CafeDevice, device_id)
        if device is None or device.status != "active":
            raise unauthenticated("Device is not active")
        kind = msg.get("type")
        if kind == "heartbeat":
            hb = HeartbeatIn.model_validate({k: v for k, v in msg.items() if k != "type"})
            service.heartbeat(session, device, client_version=hb.client_version, os_version=hb.os_version, installed=hb.installed_apps, ip=ip)
            seat = session.get(CafeSeat, device.seat_id)
            assert seat is not None
            return {"type": "status", **_status(session, DeviceContext(device=device, seat=seat))}
        if kind == "result":
            res = ResultIn.model_validate({k: v for k, v in msg.items() if k not in ("type", "command_id")})
            cmd = service.record_result(session, device, uuid.UUID(str(msg.get("command_id"))), res.ok, res.code, res.message, res.detail)
            return {"type": "ack", "command_id": str(cmd.id), "status": cmd.status}
        return {"type": "error", "code": "UNKNOWN_MESSAGE"}


@ws_router.websocket("/ws")
async def device_ws(ws: WebSocket):
    """Commands down, heartbeats and results up. First frame must be {"type":"auth","token":...} within 5 s."""
    await ws.accept()
    if not flags.enabled("devices"):
        await ws.close(code=4404, reason="FEATURE_DISABLED")
        return
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=5))
        if first.get("type") != "auth" or not isinstance(first.get("token"), str):
            raise ValueError("auth frame expected")
        device_id, exp = await run_in_threadpool(_ws_auth, first["token"])
    except (TimeoutError, ValueError, AppError, json.JSONDecodeError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await ws.close(code=4401, reason="UNAUTHENTICATED")
        return
    ip = ws.client.host if ws.client else ""
    await ws.send_json({"type": "welcome", "device_id": str(device_id)})
    poll = 0.5 if get_settings().environment != "test" else 0.05

    async def pump() -> None:
        while True:
            if time.time() >= exp:
                await ws.close(code=4401, reason="TOKEN_EXPIRED")  # the client reconnects with a fresh token
                return
            wires = await run_in_threadpool(_ws_pending, device_id)
            if wires is None:
                await ws.close(code=4403, reason="DEVICE_REVOKED")
                return
            for w in wires:
                await ws.send_json(w)
            await asyncio.sleep(poll)

    task = asyncio.create_task(pump())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                reply = await run_in_threadpool(_ws_message, device_id, msg if isinstance(msg, dict) else {}, ip)
            except (json.JSONDecodeError, ValidationError, ValueError):
                reply = {"type": "error", "code": "BAD_MESSAGE"}
            except AppError as exc:
                reply = {"type": "error", "code": exc.code, "message": exc.message}
            await ws.send_json(reply)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
