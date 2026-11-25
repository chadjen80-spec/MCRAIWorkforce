"""MCR Smart Café tables. Every row carries organization_id and every query filters on it."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UtcDateTime, utcnow

Money = Numeric(12, 2)  # THB to the satang; never float


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


# ---- food & drink orders ------------------------------------------------------------------------------------
class CafeMenuCategory(Base):
    __tablename__ = "cafe_menu_categories"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_cafe_menu_category_org_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CafeMenuItem(Base):
    """A food or drink the café sells. The price here is the only price an order ever uses."""

    __tablename__ = "cafe_menu_items"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_cafe_menu_item_price"),
        CheckConstraint("list_price IS NULL OR list_price > price", name="ck_cafe_menu_item_list_price"),
        Index("ix_cafe_menu_items_org_category", "organization_id", "category_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_menu_categories.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(300), default="")
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)  # other spoken names, for Mira
    price: Mapped[Decimal] = mapped_column(Money)
    list_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # the "was" price shown struck through on a sale
    is_new: Mapped[bool] = mapped_column(default=False)
    available: Mapped[bool] = mapped_column(default=True)  # sold out / off the menu when false
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class CafeOrder(Base):
    __tablename__ = "cafe_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_cafe_order_org_number"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_cafe_order_org_idempotency"),
        CheckConstraint("status IN ('placed', 'preparing', 'delivered', 'cancelled')", name="ck_cafe_order_status"),
        CheckConstraint("payment_status IN ('unpaid', 'paid')", name="ck_cafe_order_payment_status"),
        CheckConstraint("total >= 0", name="ck_cafe_order_total"),
        Index("ix_cafe_orders_org_status", "organization_id", "status"),
        Index("ix_cafe_orders_org_seat", "organization_id", "seat_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)  # short order number staff call out, per organisation
    seat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_seats.id", ondelete="RESTRICT"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cafe_sessions.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="placed")
    payment_status: Mapped[str] = mapped_column(String(16), default="unpaid")
    payment_method: Mapped[str] = mapped_column(String(16), default="counter")  # the only method until a provider is connected
    total: Mapped[Decimal] = mapped_column(Money)
    note: Mapped[str] = mapped_column(String(50), default="")
    via: Mapped[str] = mapped_column(String(16), default="seat")  # seat | mira | staff
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(320), default="")
    paid_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class CafeOrderLine(Base):
    """Name and price are copied from the menu when the order is placed, so later menu edits never change an order."""

    __tablename__ = "cafe_order_lines"
    __table_args__ = (
        CheckConstraint("quantity BETWEEN 1 AND 20", name="ck_cafe_order_line_quantity"),
        CheckConstraint("unit_price >= 0 AND line_total >= 0", name="ck_cafe_order_line_amounts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cafe_orders.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cafe_menu_items.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    unit_price: Mapped[Decimal] = mapped_column(Money)
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[Decimal] = mapped_column(Money)
