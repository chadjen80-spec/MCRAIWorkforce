"""Device identity. Seat PCs never use a person's token and people never use a device token.

Device tokens are signed with their own secret and carry aud=mcr-cafe-device. User tokens carry no audience and are
signed with JWT_SECRET, so `decode_access_token` rejects a device token (PyJWT refuses an unexpected `aud`) and
`decode_device_token` rejects a user token (wrong key, missing `aud`).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

import jwt
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.cafe.models import CafeDevice, CafeSeat
from app.config import get_settings
from app.db import get_db, utcnow
from app.errors import unauthenticated

DEVICE_AUD = "mcr-cafe-device"
_ALG = "HS256"


def _secret() -> str:
    s = get_settings()
    if s.cafe_device_jwt_secret:
        return s.cafe_device_jwt_secret
    return hmac.new(s.jwt_secret.encode(), b"mcr-cafe-device-token-v1", hashlib.sha256).hexdigest()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_secret(prefix: str) -> tuple[str, str]:
    value = prefix + secrets.token_urlsafe(32)
    return value, hash_secret(value)


def issue_device_token(device: CafeDevice) -> tuple[str, int]:
    minutes = get_settings().cafe_device_token_minutes
    now = utcnow()
    payload = {"sub": str(device.id), "org": str(device.organization_id), "aud": DEVICE_AUD, "typ": "device",
               "iat": int(now.timestamp()), "exp": int((now + timedelta(minutes=minutes)).timestamp())}
    return jwt.encode(payload, _secret(), algorithm=_ALG), minutes * 60


def decode_device_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALG], audience=DEVICE_AUD, options={"require": ["exp", "aud", "sub"]})
        if claims.get("typ") != "device":
            raise unauthenticated("Not a device token")
        return uuid.UUID(claims["sub"]), uuid.UUID(claims["org"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise unauthenticated("Invalid or expired device token") from exc


@dataclass
class DeviceContext:
    device: CafeDevice
    seat: CafeSeat

    @property
    def actor(self) -> str:
        return f"device:{self.device.id}"


def load_device(session: Session, token: str) -> DeviceContext:
    device_id, org_id = decode_device_token(token)
    device = session.get(CafeDevice, device_id)
    if device is None or device.organization_id != org_id or device.status != "active":
        raise unauthenticated("Device is not active")
    seat = session.get(CafeSeat, device.seat_id)
    if seat is None or seat.organization_id != org_id:
        raise unauthenticated("Device has no seat")
    return DeviceContext(device=device, seat=seat)


def get_device(authorization: str | None = Header(default=None), session: Session = Depends(get_db)) -> DeviceContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthenticated("Device token required")
    return load_device(session, authorization[7:].strip())
