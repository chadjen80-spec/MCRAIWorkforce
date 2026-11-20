"""Request and response models for /v1/qtc. Money crosses the API as strings (exact decimals)."""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.qtc.money import MoneyError, q_rate, thai_tax_id_valid, to_decimal

# Decimals are serialised as strings so no client ever parses an amount as a float.
DecimalStr = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json")]

_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def _money_in(value: object, field: str) -> Decimal:
    try:
        return to_decimal(value, field)
    except MoneyError as e:
        raise ValueError(str(e)) from e


class Page(BaseModel):
    total: int
    limit: int
    offset: int


# ---- customers ---------------------------------------------------------------
class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    line_id: str = Field(default="", max_length=80)
    is_primary: bool = False
    consent_email: bool = False
    consent_line: bool = False

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("not a valid email address")
        return v


class ContactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    line_id: str | None = Field(default=None, max_length=80)
    is_primary: bool | None = None
    consent_email: bool | None = None
    consent_line: bool | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    customer_id: uuid.UUID
    name: str
    title: str
    email: str
    phone: str
    line_id: str
    is_primary: bool
    consent_email: bool
    consent_line: bool


class _CustomerFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("tax_id", check_fields=False)
    @classmethod
    def _tax_id(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = re.sub(r"[\s-]", "", v)
        if v == "":
            return None
        if not thai_tax_id_valid(v):
            raise ValueError("Thai tax ID must be 13 digits with a valid check digit")
        return v

    @field_validator("branch", check_fields=False)
    @classmethod
    def _branch(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"\d{5}", v):
            raise ValueError("branch must be 5 digits (00000 = head office)")
        return v

    @field_validator("code", check_fields=False)
    @classmethod
    def _code(cls, v: str | None) -> str | None:
        if v is not None and not _CODE.match(v):
            raise ValueError("code: letters, digits, dot, dash or underscore, up to 32 characters")
        return v


class CustomerCreate(_CustomerFields):
    code: str | None = None  # generated (C0001...) when omitted
    name: str = Field(min_length=1, max_length=200)
    legal_name: str = Field(default="", max_length=300)
    customer_type: Literal["company", "individual"] = "company"
    tax_id: str | None = None
    branch: str = "00000"
    address: str = Field(default="", max_length=2000)
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=40)
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    notes: str = Field(default="", max_length=4000)
    contacts: list[ContactIn] = Field(default_factory=list, max_length=20)


class CustomerPatch(_CustomerFields):
    code: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=300)
    customer_type: Literal["company", "individual"] | None = None
    tax_id: str | None = None
    branch: str | None = None
    address: str | None = Field(default=None, max_length=2000)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = Field(default=None, max_length=4000)
    status: Literal["active", "archived"] | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    legal_name: str
    customer_type: str
    tax_id: str | None
    branch: str
    address: str
    email: str
    phone: str
    payment_terms_days: int
    notes: str
    status: str
    created_at: datetime
    updated_at: datetime
    contacts: list[ContactOut] = Field(default_factory=list)


class CustomerList(Page):
    items: list[CustomerOut]


# ---- services ----------------------------------------------------------------
class ServiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    name_en: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    category: str = Field(default="", max_length=80)
    unit: str = Field(default="งาน", min_length=1, max_length=40)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    vat_exempt: bool = False

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        if not _CODE.match(v):
            raise ValueError("code: letters, digits, dot, dash or underscore, up to 32 characters")
        return v

    @field_validator("keywords")
    @classmethod
    def _keywords(cls, v: list[str]) -> list[str]:
        return [k.strip()[:60] for k in v if k.strip()]


class ServicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    name_en: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    category: str | None = Field(default=None, max_length=80)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    keywords: list[str] | None = Field(default=None, max_length=30)
    vat_exempt: bool | None = None
    status: Literal["active", "archived"] | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    name_en: str
    description: str
    category: str
    unit: str
    keywords: list[str]
    vat_exempt: bool
    status: str
    created_at: datetime
    updated_at: datetime
    # The price from the organization's default active price list, when there is one.
    default_price: DecimalStr | None = None


class ServiceList(Page):
    items: list[ServiceOut]


# ---- price lists ---------------------------------------------------------------
class PriceListCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    currency: Literal["THB"] = "THB"  # other currencies need exchange-rate rules first
    prices_include_vat: bool = False
    vat_rate: Decimal = Decimal("0.0700")
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("vat_rate", mode="before")
    @classmethod
    def _rate(cls, v: object) -> Decimal:
        d = _money_in(v, "vat_rate")
        if not (Decimal("0") <= d < Decimal("1")):
            raise ValueError("vat_rate is a fraction, e.g. \"0.07\" for 7 %")
        return q_rate(d)  # stored precision: 4 places


class PriceListPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    prices_include_vat: bool | None = None
    vat_rate: Decimal | None = None
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("vat_rate", mode="before")
    @classmethod
    def _rate(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        d = _money_in(v, "vat_rate")
        if not (Decimal("0") <= d < Decimal("1")):
            raise ValueError("vat_rate is a fraction, e.g. \"0.07\" for 7 %")
        return q_rate(d)  # stored precision: 4 places


class PriceItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_price: Decimal
    notes: str = Field(default="", max_length=300)

    @field_validator("unit_price", mode="before")
    @classmethod
    def _price(cls, v: object) -> Decimal:
        d = _money_in(v, "unit_price")
        if d < 0:
            raise ValueError("unit_price cannot be negative")
        if d.as_tuple().exponent < -4:  # type: ignore[operator]
            raise ValueError("unit_price has at most 4 decimal places")
        return d.quantize(Decimal("0.0001"))  # stored precision: 4 places


class PriceItemOut(BaseModel):
    service_id: uuid.UUID
    service_code: str
    service_name: str
    unit: str
    unit_price: DecimalStr
    notes: str


class PriceListOut(BaseModel):
    id: uuid.UUID
    name: str
    currency: str
    prices_include_vat: bool
    vat_rate: DecimalStr
    valid_from: date | None
    valid_to: date | None
    status: str
    is_default: bool
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    items: list[PriceItemOut] = Field(default_factory=list)


class PriceListList(Page):
    items: list[PriceListOut]


# ---- audit ---------------------------------------------------------------------
class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    action: str
    actor: str
    detail: dict
    created_at: datetime


class AuditEventList(Page):
    items: list[AuditEventOut]


# ---- seller settings (Phase B) ---------------------------------------------------------------
class SettingsIn(_CustomerFields):
    legal_name: str = Field(default="", max_length=300)
    tax_id: str | None = None
    branch: str = "00000"
    address: str = Field(default="", max_length=2000)
    phone: str = Field(default="", max_length=40)
    email: str = Field(default="", max_length=320)
    quotation_terms: str = Field(default="", max_length=4000)
    default_valid_days: int = Field(default=30, ge=1, le=365)


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    legal_name: str
    tax_id: str | None
    branch: str
    address: str
    phone: str
    email: str
    quotation_terms: str
    default_valid_days: int


# ---- quotations (Phase B) ----------------------------------------------------------------------
def _decimal_field(v: object, field: str, *, places: int, minimum: Decimal | None = None, maximum: Decimal | None = None) -> Decimal:
    d = _money_in(v, field)
    if d.as_tuple().exponent < -places:  # type: ignore[operator]
        raise ValueError(f"{field} has at most {places} decimal places")
    if minimum is not None and d < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and d > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return d


class QuotationLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service_id: uuid.UUID | None = None
    description: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=40)
    quantity: Decimal = Decimal("1")
    unit_price: Decimal | None = None  # leave empty to use the approved price list (or to leave it for a person)
    discount_percent: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    vat_exempt: bool | None = None

    @field_validator("quantity", mode="before")
    @classmethod
    def _qty(cls, v: object) -> Decimal:
        d = _decimal_field(v, "quantity", places=3)
        if d <= 0:
            raise ValueError("quantity must be greater than zero")
        return d

    @field_validator("unit_price", mode="before")
    @classmethod
    def _price(cls, v: object) -> Decimal | None:
        if v is None or v == "":
            return None
        return _decimal_field(v, "unit_price", places=4, minimum=Decimal("0")).quantize(Decimal("0.0001"))  # stored precision

    @field_validator("discount_percent", mode="before")
    @classmethod
    def _pct(cls, v: object) -> Decimal:
        return _decimal_field(v, "discount_percent", places=4, minimum=Decimal("0"), maximum=Decimal("100"))

    @field_validator("discount_amount", mode="before")
    @classmethod
    def _amt(cls, v: object) -> Decimal:
        return _decimal_field(v, "discount_amount", places=2, minimum=Decimal("0"))


class QuotationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    price_list_id: uuid.UUID | None = None  # default: the approved default price list
    title: str = Field(default="", max_length=200)
    brief: str = Field(default="", max_length=4000)
    lines: list[QuotationLineIn] | None = Field(default=None, max_length=100)
    match_brief: bool = True  # with no lines: one line per catalogue service named in the brief
    valid_until: date | None = None
    notes: str = Field(default="", max_length=4000)
    terms: str | None = Field(default=None, max_length=4000)


class QuotationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contact_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)
    lines: list[QuotationLineIn] | None = Field(default=None, max_length=100)
    valid_until: date | None = None
    notes: str | None = Field(default=None, max_length=4000)
    terms: str | None = Field(default=None, max_length=4000)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "changes_requested"]
    comment: str = Field(default="", max_length=2000)


class SendIn(BaseModel):
    """Records that the approved quotation was given to the customer. The system itself sends nothing."""

    model_config = ConfigDict(extra="forbid")
    method: Literal["email_by_user", "line_by_user", "in_person", "printed", "other"]
    note: str = Field(default="", max_length=1000)


class AcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted_by_name: str = Field(min_length=1, max_length=200)
    method: Literal["signed_document", "purchase_order", "email", "line", "verbal", "other"]
    reference: str = Field(default="", max_length=200)  # PO number, email subject, …
    note: str = Field(default="", max_length=2000)
    evidence_url: str | None = Field(default=None, max_length=1000)  # where the signed copy / PO is kept
    revision: int | None = None  # the revision the customer accepted (must be the current one)


class DeclineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=2000)


class ReviseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)


class QuotationLineOut(BaseModel):
    position: int
    service_id: uuid.UUID | None
    description: str
    unit: str
    quantity: DecimalStr
    unit_price: DecimalStr | None
    price_source: str
    discount_percent: DecimalStr
    discount_amount: DecimalStr
    vat_exempt: bool
    line_net: DecimalStr
    line_vat: DecimalStr
    line_total: DecimalStr


class QuotationOut(BaseModel):
    id: uuid.UUID
    number: str
    revision: int
    status: str  # includes the computed "expired"
    customer: dict
    contact_id: uuid.UUID | None
    price_list_id: uuid.UUID
    title: str
    brief: str
    currency: str
    prices_include_vat: bool
    vat_rate: DecimalStr
    issue_date: date
    valid_until: date
    notes: str
    terms: str
    subtotal: DecimalStr
    discount_total: DecimalStr
    vat_total: DecimalStr
    grand_total: DecimalStr
    amount_in_words: str
    missing_prices: int
    created_by: str
    submitted_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    sent_at: datetime | None
    sent_method: str | None
    accepted_at: datetime | None
    accepted_revision: int | None
    acceptance: dict | None
    created_at: datetime
    updated_at: datetime
    lines: list[QuotationLineOut] = Field(default_factory=list)
    matched: list[dict] = Field(default_factory=list)  # services found in the brief (on creation)


class QuotationList(Page):
    items: list[QuotationOut]


class RevisionOut(BaseModel):
    revision: int
    grand_total: DecimalStr
    reason: str
    created_by: str
    created_at: datetime


class ApprovalOut(BaseModel):
    revision: int
    decision: str
    comment: str
    decided_by: str
    decided_at: datetime
