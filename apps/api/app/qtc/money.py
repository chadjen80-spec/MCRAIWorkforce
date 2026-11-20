"""Deterministic money arithmetic for Quote-to-Cash.

Rules (used by every quotation, order, invoice and payment calculation):
- Only `Decimal`, never float. Inputs are converted with `to_decimal`, which rejects floats outright so a
  binary rounding error can never enter a financial amount.
- Amounts are rounded to 2 decimal places (satang) with ROUND_HALF_UP, the rounding used on Thai
  commercial documents. Rounding happens per line (line net and line VAT), and document totals are the
  sum of the rounded lines, so a document always adds up exactly as printed.
- VAT is either added on top of net prices (`prices_include_vat=False`) or extracted from VAT-inclusive
  prices (`prices_include_vat=True`). Either way the result reports net, VAT and gross per line.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TWO = Decimal("0.01")
RATE_PLACES = Decimal("0.0001")
QTY_PLACES = Decimal("0.001")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
DEFAULT_VAT_RATE = Decimal("0.0700")  # Thailand's standard VAT rate at the time of writing; configurable per price list


class MoneyError(ValueError):
    pass


def to_decimal(value: object, field: str = "amount") -> Decimal:
    """Strict conversion: str / int / Decimal only. Floats are refused (they cannot represent 0.1 exactly)."""
    if isinstance(value, bool) or isinstance(value, float):
        raise MoneyError(f"{field}: send money and quantities as strings or integers, not floating-point numbers")
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, int | str):
        try:
            d = Decimal(str(value).strip())
        except InvalidOperation as e:
            raise MoneyError(f"{field}: {value!r} is not a number") from e
    else:
        raise MoneyError(f"{field}: unsupported type {type(value).__name__}")
    if not d.is_finite():
        raise MoneyError(f"{field}: must be a finite number")
    return d


def q2(value: Decimal) -> Decimal:
    """Round to satang, half up."""
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def q_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_PLACES, rounding=ROUND_HALF_UP)


def q_qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class LineAmounts:
    gross_before_discount: Decimal  # quantity x unit price, as priced (net or VAT-inclusive)
    discount: Decimal  # money taken off, in the same basis as the unit price
    net: Decimal  # excluding VAT
    vat: Decimal
    total: Decimal  # including VAT


def line_amounts(
    quantity: Decimal,
    unit_price: Decimal,
    *,
    discount_percent: Decimal = Decimal("0"),
    discount_amount: Decimal = Decimal("0"),
    vat_rate: Decimal = DEFAULT_VAT_RATE,
    prices_include_vat: bool = False,
    vat_exempt: bool = False,
) -> LineAmounts:
    """One line, rounded per line. Percent discount applies first, then a fixed amount."""
    if quantity <= 0:
        raise MoneyError("quantity must be greater than zero")
    if unit_price < 0:
        raise MoneyError("unit price cannot be negative")
    if not (Decimal("0") <= discount_percent <= HUNDRED):
        raise MoneyError("discount percent must be between 0 and 100")
    if discount_amount < 0:
        raise MoneyError("discount amount cannot be negative")
    if vat_rate < 0 or vat_rate >= 1:
        raise MoneyError("VAT rate must be a fraction such as 0.07")
    rate = Decimal("0") if vat_exempt else vat_rate
    base = q2(quantity * unit_price)
    pct_off = q2(base * discount_percent / HUNDRED)
    discount = pct_off + q2(discount_amount)
    if discount > base:
        raise MoneyError("discount cannot be larger than the line amount")
    after = base - discount
    if prices_include_vat:
        total = after
        net = q2(total / (Decimal("1") + rate))
        vat = total - net
    else:
        net = after
        vat = q2(net * rate)
        total = net + vat
    return LineAmounts(gross_before_discount=base, discount=discount, net=net, vat=vat, total=total)


@dataclass(frozen=True)
class DocumentTotals:
    subtotal: Decimal  # sum of line nets (excluding VAT)
    discount: Decimal
    vat: Decimal
    total: Decimal


def document_totals(lines: list[LineAmounts]) -> DocumentTotals:
    """Totals are sums of rounded lines, so the printed document always adds up."""
    return DocumentTotals(
        subtotal=sum((x.net for x in lines), ZERO),
        discount=sum((x.discount for x in lines), ZERO),
        vat=sum((x.vat for x in lines), ZERO),
        total=sum((x.total for x in lines), ZERO),
    )


def thai_tax_id_valid(tax_id: str) -> bool:
    """13-digit Thai taxpayer / citizen ID with its check digit."""
    if len(tax_id) != 13 or not tax_id.isdigit():
        return False
    total = sum(int(tax_id[i]) * (13 - i) for i in range(12))
    return (11 - total % 11) % 10 == int(tax_id[12])
