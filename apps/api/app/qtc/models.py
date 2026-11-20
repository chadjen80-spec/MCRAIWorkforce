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
    Integer,
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


# ---- Phase B: quotations ------------------------------------------------------------------
class QtcSettings(Base):
    """The seller's details printed on commercial documents, and Quote-to-Cash defaults (one row per org)."""

    __tablename__ = "qtc_settings"

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(300), default="")
    tax_id: Mapped[str | None] = mapped_column(String(13), nullable=True)
    branch: Mapped[str] = mapped_column(String(5), default="00000")
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    quotation_terms: Mapped[str] = mapped_column(Text, default="")
    default_valid_days: Mapped[int] = mapped_column(default=30)
    updated_by: Mapped[str] = mapped_column(String(320), default="")
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class QuotationStatus:
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    ALL = frozenset({DRAFT, PENDING_APPROVAL, APPROVED, SENT, ACCEPTED, DECLINED, CANCELLED})
    # "expired" is not stored: an approved or sent quotation past valid_until is reported as expired.


_STATUS_CHECK = "status IN ('draft', 'pending_approval', 'approved', 'sent', 'accepted', 'declined', 'cancelled')"


class Quotation(Base):
    __tablename__ = "quotations"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_quotation_org_number"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_quotation_org_idempotency"),
        CheckConstraint(_STATUS_CHECK, name="ck_quotation_status"),
        CheckConstraint("subtotal >= 0 AND vat_total >= 0 AND grand_total >= 0 AND discount_total >= 0", name="ck_quotation_totals"),
        Index("ix_quotations_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(default=1)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_contacts.id", ondelete="SET NULL"), nullable=True)
    price_list_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("price_lists.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(200), default="")
    brief: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=QuotationStatus.DRAFT)
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    prices_include_vat: Mapped[bool] = mapped_column(Boolean, default=False)
    vat_rate: Mapped[Decimal] = mapped_column(Rate, default=Decimal("0.0700"))
    issue_date: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    terms: Mapped[str] = mapped_column(Text, default="")
    subtotal: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    vat_total: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    grand_total: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    created_by: Mapped[str] = mapped_column(String(320), default="")
    submitted_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    sent_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sent_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    accepted_revision: Mapped[int | None] = mapped_column(nullable=True)
    acceptance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # who, how, reference, evidence
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class QuotationLine(Base):
    __tablename__ = "quotation_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_quotation_line_quantity"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="ck_quotation_line_price"),
        CheckConstraint("discount_percent >= 0 AND discount_percent <= 100 AND discount_amount >= 0", name="ck_quotation_line_discount"),
        CheckConstraint("price_source IN ('price_list', 'manual', 'missing')", name="ck_quotation_line_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    quotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(default=1)
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_catalog.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(40), default="งาน")
    quantity: Mapped[Decimal] = mapped_column(Quantity, default=Decimal("1"))
    unit_price: Mapped[Decimal | None] = mapped_column(Price, nullable=True)  # NULL: a person must enter it
    price_source: Mapped[str] = mapped_column(String(16), default="missing")
    discount_percent: Mapped[Decimal] = mapped_column(Rate, default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    vat_exempt: Mapped[bool] = mapped_column(Boolean, default=False)
    line_net: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    line_vat: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))


class QuotationRevision(Base):
    """Frozen copy of a quotation as submitted: what was approved, sent and accepted is never edited later."""

    __tablename__ = "quotation_revisions"
    __table_args__ = (UniqueConstraint("quotation_id", "revision", name="uq_quotation_revision"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    quotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column()
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    grand_total: Mapped[Decimal] = mapped_column(Amount)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class QuotationApproval(Base):
    __tablename__ = "quotation_approvals"
    __table_args__ = (CheckConstraint("decision IN ('approved', 'changes_requested')", name="ck_quotation_approval_decision"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    quotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotations.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column()
    decision: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(String(320))
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# ---- Phase C: orders, invoices, payments ------------------------------------------------------
class SalesOrder(Base):
    """Created from an accepted quotation. One order per quotation, enforced by the database."""

    __tablename__ = "sales_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_sales_order_org_number"),
        UniqueConstraint("quotation_id", name="uq_sales_order_quotation"),
        CheckConstraint("status IN ('open', 'completed', 'cancelled')", name="ck_sales_order_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(32))
    quotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotations.id", ondelete="RESTRICT"))
    quotation_revision: Mapped[int] = mapped_column()
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    currency: Mapped[str] = mapped_column(String(3), default="THB")
    prices_include_vat: Mapped[bool] = mapped_column(Boolean, default=False)
    vat_rate: Mapped[Decimal] = mapped_column(Rate)
    subtotal: Mapped[Decimal] = mapped_column(Amount)
    vat_total: Mapped[Decimal] = mapped_column(Amount)
    grand_total: Mapped[Decimal] = mapped_column(Amount)
    accepted_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)  # the quotation revision the customer accepted
    notes: Mapped[str] = mapped_column(Text, default="")
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class SalesOrderLine(Base):
    __tablename__ = "sales_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column()
    service_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("service_catalog.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(Price)
    line_net: Mapped[Decimal] = mapped_column(Amount)
    line_vat: Mapped[Decimal] = mapped_column(Amount)
    line_total: Mapped[Decimal] = mapped_column(Amount)


class SalesOrderMilestone(Base):
    """Billing schedule (deposit / installment / final, adding up exactly to the order total) and delivery
    milestones (one per line of work, amount 0)."""

    __tablename__ = "sales_order_milestones"
    __table_args__ = (
        CheckConstraint("kind IN ('deposit', 'installment', 'final', 'delivery')", name="ck_milestone_kind"),
        CheckConstraint("status IN ('pending', 'invoiced', 'done', 'cancelled')", name="ck_milestone_status"),
        CheckConstraint("amount_total >= 0 AND amount_net >= 0 AND amount_vat >= 0", name="ck_milestone_amounts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(String(300))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount_net: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    amount_vat: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    amount_total: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    done_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


_INVOICE_STATUS = "status IN ('draft', 'issued', 'partially_paid', 'paid', 'cancelled')"


class Invoice(Base):
    """A commercial invoice (ใบแจ้งหนี้). Not a tax invoice or receipt: those are separate documents whose
    issuance stays disabled until Thai legal and accounting requirements are verified."""

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_invoice_org_number"),
        CheckConstraint(_INVOICE_STATUS, name="ck_invoice_status"),
        CheckConstraint("document_type IN ('commercial_invoice')", name="ck_invoice_document_type"),
        CheckConstraint("paid_amount >= 0 AND paid_amount <= total", name="ck_invoice_paid_within_total"),
        CheckConstraint("total >= 0 AND subtotal >= 0 AND vat_total >= 0", name="ck_invoice_totals"),
        # one live invoice per billing milestone (a cancelled one may be replaced)
        Index("uq_invoice_live_milestone", "milestone_id", unique=True,
              postgresql_where=text("status <> 'cancelled'"), sqlite_where=text("status <> 'cancelled'")),
        Index("ix_invoices_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[str | None] = mapped_column(String(32), nullable=True)  # assigned when issued
    document_type: Mapped[str] = mapped_column(String(32), default="commercial_invoice")
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id", ondelete="RESTRICT"), index=True)
    milestone_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_order_milestones.id", ondelete="RESTRICT"))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    vat_rate: Mapped[Decimal] = mapped_column(Rate)
    prices_include_vat: Mapped[bool] = mapped_column(Boolean, default=False)
    subtotal: Mapped[Decimal] = mapped_column(Amount)
    vat_total: Mapped[Decimal] = mapped_column(Amount)
    total: Mapped[Decimal] = mapped_column(Amount)
    paid_amount: Mapped[Decimal] = mapped_column(Amount, default=Decimal("0"))
    notes: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # frozen at issue
    created_by: Mapped[str] = mapped_column(String(320))
    issued_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    cancelled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column()
    description: Mapped[str] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(String(40))
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(Price)
    line_net: Mapped[Decimal] = mapped_column(Amount)
    line_vat: Mapped[Decimal] = mapped_column(Amount)
    line_total: Mapped[Decimal] = mapped_column(Amount)


class PaymentRecord(Base):
    """Money received. Recorded by a person, applied to invoices only when an owner or admin confirms it."""

    __tablename__ = "payment_records"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_payment_org_idempotency"),
        UniqueConstraint("organization_id", "number", name="uq_payment_org_number"),
        CheckConstraint("amount > 0", name="ck_payment_amount"),
        CheckConstraint("status IN ('pending_confirmation', 'confirmed', 'rejected')", name="ck_payment_status"),
        CheckConstraint("method IN ('bank_transfer', 'cash', 'cheque', 'card', 'promptpay', 'other')", name="ck_payment_method"),
        # the same bank / cheque reference cannot be recorded twice (rejected entries do not count)
        Index("uq_payment_live_reference", "organization_id", "method", "reference", unique=True,
              postgresql_where=text("reference <> '' AND status <> 'rejected'"),
              sqlite_where=text("reference <> '' AND status <> 'rejected'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(32))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Amount)
    received_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(16))
    reference: Mapped[str] = mapped_column(String(120), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    evidence_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    evidence_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evidence_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending_confirmation")
    recorded_by: Mapped[str] = mapped_column(String(320))
    confirmed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        UniqueConstraint("payment_id", "invoice_id", name="uq_payment_allocation"),
        CheckConstraint("amount > 0", name="ck_payment_allocation_amount"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_records.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Amount)


class PaymentReminder(Base):
    """Collection reminder drafts (written by the Collection Agent in Phase D). Nothing is sent without an
    approved draft, a channel the contact consented to, and an enabled, scoped sending integration."""

    __tablename__ = "payment_reminders"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'approved', 'sent', 'cancelled')", name="ck_reminder_status"),
        CheckConstraint("channel IN ('email', 'line', 'manual')", name="ck_reminder_channel"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customer_contacts.id", ondelete="SET NULL"), nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="manual")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    body: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320))
    approved_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class QtcEvent(Base):
    """Transactional outbox. A business event is written in the same transaction as the change it reports and is
    delivered afterwards to n8n as a signed webhook (`app.qtc.events`). `dedupe_key` makes each event happen once
    (one "overdue" alert per invoice and due date, one weekly summary per ISO week, and so on)."""

    __tablename__ = "qtc_events"
    __table_args__ = (
        UniqueConstraint("organization_id", "dedupe_key", name="uq_qtc_event_dedupe"),
        CheckConstraint("status IN ('pending', 'delivered', 'failed')", name="ck_qtc_event_status"),
        Index("ix_qtc_events_status_next", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
