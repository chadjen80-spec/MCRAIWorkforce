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
