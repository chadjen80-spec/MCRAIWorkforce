"""Quote-to-Cash tables. Every tenant-owned row carries organization_id; money is NUMERIC, never float.

Phase A: customers and contacts, the service catalogue, price lists (rate cards) and their items, and the
QTC audit trail. Later phases add quotations, orders, invoices and payments in their own migrations.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UtcDateTime, utcnow

Amount = Numeric(18, 2)  # currency amounts (THB to the satang)
Price = Numeric(18, 4)  # unit prices may carry more precision than a total
Quantity = Numeric(14, 3)
Rate = Numeric(7, 4)  # 0.0700 = 7 %


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_customer_org_code"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_customer_org_idempotency"),
        CheckConstraint("customer_type IN ('company', 'individual')", name="ck_customer_type"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_customer_status"),
        Index("ix_customers_org_name", "organization_id", "name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    legal_name: Mapped[str] = mapped_column(String(300), default="")
    customer_type: Mapped[str] = mapped_column(String(16), default="company")
    tax_id: Mapped[str | None] = mapped_column(String(13), nullable=True)
    branch: Mapped[str] = mapped_column(String(5), default="00000")  # 00000 = head office
    address: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    payment_terms_days: Mapped[int] = mapped_column(default=30)
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(320), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class CustomerContact(Base):
    __tablename__ = "customer_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    line_id: Mapped[str] = mapped_column(String(80), default="")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    # Collection reminders may only go out through channels the contact agreed to (Phase D/E).
    consent_email: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_line: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class ServiceItem(Base):
    """A service the organization sells. Prices live in price lists, never on the service itself."""

    __tablename__ = "service_catalog"
    __table_args__ = (
        UniqueConstraint("organization_id", "code", name="uq_service_org_code"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_service_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(80), default="")
    unit: Mapped[str] = mapped_column(String(40), default="งาน")
    # Words the quotation engine may use to match a customer's brief to this service (never to price it).
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    vat_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class PriceList(Base):
    """A rate card. Only an approved (active) price list may price a quotation."""

    __tablename__ = "price_lists"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'active', 'archived')", name="ck_price_list_status"),
        CheckConstraint("vat_rate >= 0 AND vat_rate < 1", name="ck_price_list_vat_rate"),
        UniqueConstraint("organization_id", "name", name="uq_price_list_org_name"),
        # At most one default price list per organization, enforced by the database.
        Index("uq_price_list_one_default", "organization_id", unique=True,
              postgresql_where=text("is_default"), sqlite_where=text("is_default")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    prices_include_vat: Mapped[bool] = mapped_column(Boolean, default=False)
    vat_rate: Mapped[Decimal] = mapped_column(Rate, default=Decimal("0.0700"))
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class PriceListItem(Base):
    __tablename__ = "price_list_items"
    __table_args__ = (
        UniqueConstraint("price_list_id", "service_id", name="uq_price_list_item_service"),
        CheckConstraint("unit_price >= 0", name="ck_price_list_item_price"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    price_list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("price_lists.id", ondelete="CASCADE"), index=True)
    service_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("service_catalog.id", ondelete="RESTRICT"), index=True)
    unit_price: Mapped[Decimal] = mapped_column(Price)
    notes: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class NumberSequence(Base):
    """Per-organization counters for customer codes and document numbers (QT-, SO-, INV- … in later phases).

    A number is taken by locking the row (SELECT … FOR UPDATE) and incrementing it in the caller's
    transaction, so concurrent requests can never receive the same number; the unique constraints on the
    numbered tables are the second line of defence.
    """

    __tablename__ = "qtc_number_sequences"
    __table_args__ = (UniqueConstraint("organization_id", "kind", name="uq_number_sequence_org_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    next_value: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class QtcAuditEvent(Base):
    """Append-only history of every commercial change (who, what, when, before/after)."""

    __tablename__ = "qtc_audit_events"
    __table_args__ = (Index("ix_qtc_audit_entity", "organization_id", "entity_type", "entity_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(60))
    actor: Mapped[str] = mapped_column(String(320))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)
