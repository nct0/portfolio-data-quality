"""Safe, deterministic normalization and strict parsing of raw text values.

Nothing here guesses. A value either normalizes to exactly one result under the agreed rules or it is
left for validation to reject.
"""

from __future__ import annotations

import re
from datetime import datetime

from order_quality.config import alias_key

# Normalization kinds, counted per field in the quality report.
TRIMMED = "whitespace_trimmed"
UPPERCASED = "uppercased"
CASE_STANDARDIZED = "case_standardized"
ALIAS_MAPPED = "alias_mapped"
DATE_TO_ISO = "date_to_iso"

MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")
_FULL = {name: i for i, name in enumerate(MONTHS, start=1)}
_ABBR = {name[:3]: i for i, name in enumerate(MONTHS, start=1)}
_WORD = re.compile(r"[A-Za-z]+")
_DIGITS = re.compile(r"[0-9]{1,9}")
_AMOUNT = re.compile(r"(?P<whole>[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,12})(?:\.(?P<frac>[0-9]*))?")


def parse_date(value: str, formats: tuple[str, ...]) -> str | None:
    """ISO date for the first format that matches, else None. English month names on every locale."""
    for fmt in formats:
        iso = _parse_with(value, fmt)
        if iso is not None:
            return iso
    return None


def _parse_with(value: str, fmt: str) -> str | None:
    if "%b" in fmt or "%B" in fmt:
        match = _WORD.search(value)
        if match is None:
            return None
        names = _ABBR if "%b" in fmt else _FULL
        month = names.get(match.group(0).casefold())
        if month is None:
            return None
        value = f"{value[:match.start()]}{month:02d}{value[match.end():]}"
        fmt = fmt.replace("%b", "%m").replace("%B", "%m")
    try:
        return datetime.strptime(value, fmt).date().isoformat()
    except ValueError:
        return None


def parse_quantity(value: str) -> int | None:
    """A whole number written with ASCII digits only ('3'; not '3.0', '+3', '1_0' or other scripts' digits)."""
    return int(value) if _DIGITS.fullmatch(value) else None


def parse_amount_cents(value: str, max_decimals: int) -> int | None:
    """Non-negative amount in cents. Thousands separators only in groups of three; no exponents, no rounding."""
    match = _AMOUNT.fullmatch(value)
    if match is None:
        return None
    frac = match.group("frac") or ""
    if len(frac) > max_decimals:
        return None
    return int(match.group("whole").replace(",", "")) * 100 + int(frac.ljust(2, "0") or "0")


def format_cents(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


def canonical_value(value: str, canonical: dict[str, str], aliases: dict[str, str]) -> tuple[str | None, str | None]:
    """Map a value to its canonical spelling. Returns (canonical or None, normalization kind or None)."""
    key = alias_key(value)
    if key in canonical:
        name = canonical[key]
        return name, (CASE_STANDARDIZED if name != value else None)
    if key in aliases:
        return aliases[key], ALIAS_MAPPED
    return None, None
