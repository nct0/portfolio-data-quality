"""Business rules for one order row: normalize, then validate. Pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from order_quality import errors as codes
from order_quality.config import Rules, alias_key
from order_quality.normalize import (DATE_TO_ISO, TRIMMED, UPPERCASED, canonical_value, format_cents, parse_amount_cents,
                                     parse_date, parse_quantity)
from order_quality.reference import Reference

CENT = Decimal("0.01")


@dataclass(frozen=True)
class CheckedRow:
    order_id: str
    customer_id: str
    order_date: str  # ISO date, or "" when invalid
    sku: str
    quantity: int | None
    amount_cents: int | None
    channel: str  # canonical name, or "" when unknown
    status: str  # canonical name, or "" when unknown
    issues: tuple[str, ...]
    normalizations: tuple[tuple[str, str], ...]  # (field, kind)

    def business_value(self, field: str) -> str:
        if field == "quantity":
            return "" if self.quantity is None else str(self.quantity)
        if field == "amount_usd":
            return "" if self.amount_cents is None else format_cents(self.amount_cents)
        return getattr(self, field)


class RowChecker:
    """Normalizes and validates rows against one set of rules and reference data."""

    def __init__(self, rules: Rules, reference: Reference) -> None:
        self.rules = rules
        self.reference = reference
        self.statuses = {alias_key(s): s for s in rules.statuses}

    def check(self, raw: dict[str, str], malformed: bool = False) -> CheckedRow:
        rules, reference = self.rules, self.reference
        events: list[tuple[str, str]] = []
        text: dict[str, str] = {}
        for field, value in raw.items():
            if rules.trim_whitespace and value != value.strip():
                value = value.strip()
                events.append((field, TRIMMED))
            if field in rules.uppercase and value != value.upper():
                value = value.upper()
                events.append((field, UPPERCASED))
            text[field] = value

        order_date = parse_date(text["order_date"], rules.date_formats) or ""
        if order_date and order_date != text["order_date"]:
            events.append(("order_date", DATE_TO_ISO))
        quantity = parse_quantity(text["quantity"])
        if quantity is not None and (quantity < rules.quantity_min or
                                     (rules.quantity_max is not None and quantity > rules.quantity_max)):
            quantity = None
        amount = parse_amount_cents(text["amount_usd"], rules.max_decimals)
        channel, kind = canonical_value(text["channel"], reference.channels, rules.channel_aliases)
        if kind:
            events.append(("channel", kind))
        status, kind = canonical_value(text["status"], self.statuses, rules.status_aliases)
        if kind:
            events.append(("status", kind))

        if malformed:  # positional values of a broken record are unreliable: report only the parse problem
            return CheckedRow(
                order_id=text["order_id"], customer_id=text["customer_id"], order_date=order_date, sku=text["sku"],
                quantity=quantity, amount_cents=amount, channel=channel or "", status=status or "",
                issues=(codes.MALFORMED_ROW,), normalizations=(),
            )
        issues: list[str] = []
        missing = {f for f in rules.required if not text[f]}
        issues.extend(f"{codes.MISSING_PREFIX}{f.upper()}" for f in rules.required if f in missing)
        if not order_date and "order_date" not in missing:
            issues.append(codes.INVALID_DATE)
        if text["sku"] not in reference.prices and "sku" not in missing:
            issues.append(codes.UNKNOWN_SKU)
        if quantity is None and "quantity" not in missing:
            issues.append(codes.INVALID_QUANTITY)
        if amount is None and "amount_usd" not in missing:
            issues.append(codes.INVALID_AMOUNT)
        if channel is None and "channel" not in missing:
            issues.append(codes.UNKNOWN_CHANNEL)
        if status is None and "status" not in missing:
            issues.append(codes.UNKNOWN_STATUS)
        if text["sku"] in reference.prices and quantity is not None and amount is not None:
            expected = (reference.prices[text["sku"]] * quantity).quantize(CENT)
            if abs(Decimal(amount) / 100 - expected) > rules.tolerance:
                issues.append(codes.AMOUNT_MISMATCH)

        return CheckedRow(
            order_id=text["order_id"], customer_id=text["customer_id"], order_date=order_date, sku=text["sku"],
            quantity=quantity, amount_cents=amount, channel=channel or "", status=status or "",
            issues=tuple(issues), normalizations=tuple(events),
        )


def check_row(raw: dict[str, str], rules: Rules, reference: Reference, malformed: bool = False) -> CheckedRow:
    return RowChecker(rules, reference).check(raw, malformed)


def fingerprint(row: CheckedRow, fields: tuple[str, ...]) -> str:
    """Identity of an order version: the compared business values, separated by a unit separator."""
    return "\x1f".join(row.business_value(f) for f in fields)
