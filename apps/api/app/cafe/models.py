"""MCR Smart Café tables. Every row carries organization_id and every query filters on it."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UtcDateTime, utcnow


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class CafeSeat(Base):
    __tablename__ = "cafe_seats"
    __table_args__ = (
        UniqueConstraint("organization_id", "label", name="uq_cafe_seat_org_label"),
        CheckConstraint("status IN ('active', 'maintenance', 'retired')", name="ck_cafe_seat_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(32))
    zone: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CafeEnrollmentCode(Base):
    """One-time code an administrator gives a seat PC to register. Only a hash is stored."""

    __tablename__ = "cafe_enrollment_codes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_seats.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CafeDevice(Base):
    __tablename__ = "cafe_devices"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="ck_cafe_device_status"),
        Index("ix_cafe_devices_org_seat", "organization_id", "seat_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_seats.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128), default="")
    secret_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")
    client_version: Mapped[str] = mapped_column(String(32), default="")
    os_version: Mapped[str] = mapped_column(String(128), default="")
    # {"app_key": "installed" | "missing"} as last reported by the client (from its local allowlist)
    installed_apps: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_ip: Mapped[str] = mapped_column(String(64), default="")
    enrolled_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class CafeApp(Base):
    """An approved application in the café's catalogue. Seat PCs map app_key to a local path themselves."""

    __tablename__ = "cafe_apps"
    __table_args__ = (UniqueConstraint("organization_id", "app_key", name="uq_cafe_app_org_key"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    app_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)  # extra spoken names, e.g. Thai spellings
    category: Mapped[str] = mapped_column(String(32), default="game")
    enabled: Mapped[bool] = mapped_column(default=True)
    requires_session: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CafeSession(Base):
    __tablename__ = "cafe_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'ended')", name="ck_cafe_session_status"),
        CheckConstraint("granted_seconds >= 0", name="ck_cafe_session_granted"),
        Index("ix_cafe_sessions_org_seat_status", "organization_id", "seat_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_seats.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cafe_devices.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    granted_seconds: Mapped[int] = mapped_column(Integer, default=0)
    customer_ref: Mapped[str] = mapped_column(String(64), default="")  # "guest" or a member id (later)
    started_by: Mapped[str] = mapped_column(String(320), default="")
    started_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class CafeCommand(Base):
    __tablename__ = "cafe_commands"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_cafe_command_org_idempotency"),
        CheckConstraint("status IN ('queued', 'delivered', 'succeeded', 'failed', 'expired')", name="ck_cafe_command_status"),
        Index("ix_cafe_commands_device_status", "device_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_devices.id", ondelete="CASCADE"))
    seat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_seats.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(32))
    args: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    envelope: Mapped[str] = mapped_column(Text)  # canonical JSON that was signed
    signature: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(320), default="")
    via: Mapped[str] = mapped_column(String(16), default="ui")  # ui | mira
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class CafeAssistRequest(Base):
    __tablename__ = "cafe_assist_requests"
    __table_args__ = (CheckConstraint("status IN ('open', 'resolved')", name="ck_cafe_assist_status"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    seat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_seats.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cafe_devices.id", ondelete="SET NULL"), nullable=True)
    message: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(16), default="seat")  # seat | mira | staff
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_by: Mapped[str] = mapped_column(String(320), default="")
    resolved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class CafeAuditEvent(Base):
    __tablename__ = "cafe_audit_events"
    __table_args__ = (Index("ix_cafe_audit_org_created", "organization_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(320))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
