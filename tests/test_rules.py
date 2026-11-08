"""Normalization and validation rules, one value at a time."""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import REFERENCE

from order_quality.config import default_rules
from order_quality.normalize import parse_amount_cents, parse_date, parse_quantity
from order_quality.reference import load_reference
from order_quality.validate import check_row

RULES = default_rules()
REF = load_reference(REFERENCE, RULES)
FIELDS = ("order_id", "customer_id", "order_date", "sku", "quantity", "amount_usd", "channel", "status")


def raw(**overrides):
    values = dict(zip(FIELDS, ("ORD-1", "C-1", "2026-01-05", "SKU-001", "2", "25.00", "Web", "Completed")))
    values.update(overrides)
    return values


@pytest.mark.parametrize("text, cents", [
    ("12.50", 1250), ("12.5", 1250), ("12", 1200), ("0.00", 0), ("1,234.50", 123450), ("12,345,678.00", 1234567800),
    ("1,2,3", None), ("12,34", None), ("1e2", None), ("12.505", None), ("-5.00", None), ("n/a", None), ("", None),
    ("NaN", None), ("Infinity", None), ("$12.50", None), (".50", None), ("12 50", None),
])
def test_amounts_are_parsed_strictly_without_rounding(text, cents):
    assert parse_amount_cents(text, 2) == cents


@pytest.mark.parametrize("text, quantity", [
    ("3", 3), ("03", 3), ("12", 12), ("٣", None), ("३", None), ("1_0", None), ("3.0", None), ("+3", None),
    ("-1", None), ("", None), ("three", None),
])
def test_quantities_accept_ascii_digits_only(text, quantity):
    assert parse_quantity(text) == quantity


@pytest.mark.parametrize("text, iso", [
    ("2026-02-27", "2026-02-27"), ("02/27/2026", "2026-02-27"), ("27-Feb-2026", "2026-02-27"),
    ("27-feb-2026", "2026-02-27"), ("2026-2-7", "2026-02-07"), ("27/02/2026", None), ("2026/02/27", None),
    ("27-February-2026", None), ("02/30/2026", None), ("not-a-date", None), ("", None), ("02/27/26", None),
])
def test_only_agreed_date_formats_are_accepted(text, iso):
    assert parse_date(text, RULES.date_formats) == iso


def test_valid_row_is_normalized():
    row = check_row(raw(order_id=" ord-1 ", sku="sku-001", channel=" web ", status="COMPLETED", order_date="01/05/2026"),
                    RULES, REF)
    assert row.issues == ()
    assert (row.order_id, row.sku, row.channel, row.status, row.order_date) == (
        "ORD-1", "SKU-001", "Web", "Completed", "2026-01-05")
    assert ("order_id", "whitespace_trimmed") in row.normalizations
    assert ("order_date", "date_to_iso") in row.normalizations


def test_approved_aliases_are_safe_normalizations():
    row = check_row(raw(channel="Online", status="complete"), RULES, REF)
    assert row.issues == () and row.channel == "Web" and row.status == "Completed"
    assert ("channel", "alias_mapped") in row.normalizations and ("status", "alias_mapped") in row.normalizations


def test_issue_codes_keep_the_original_order():
    row = check_row(raw(order_id="", customer_id="", order_date="x", sku="SKU-X", quantity="0", amount_usd="n/a",
                        channel="?", status="?"), RULES, REF)
    assert row.issues == ("MISSING_ORDER_ID", "MISSING_CUSTOMER_ID", "INVALID_DATE", "UNKNOWN_SKU", "INVALID_QUANTITY",
                          "INVALID_AMOUNT", "UNKNOWN_CHANNEL", "UNKNOWN_STATUS")


@pytest.mark.parametrize("amount, issues", [("25.00", ()), ("25.01", ()), ("24.99", ()), ("25.02", ("AMOUNT_MISMATCH",)),
                                            ("9999.99", ("AMOUNT_MISMATCH",))])
def test_amount_tolerance_boundary(amount, issues):
    assert check_row(raw(amount_usd=amount), RULES, REF).issues == issues


def test_zero_and_negative_quantities_are_rejected():
    assert check_row(raw(quantity="0"), RULES, REF).issues == ("INVALID_QUANTITY",)
    assert check_row(raw(quantity="-2"), RULES, REF).issues == ("INVALID_QUANTITY",)


def test_zero_quantity_is_rejected_under_python_optimize_mode():
    """The original used `assert qty > 0`, which `python -O` removes: 10 baseline rows were then accepted."""
    code = ("from order_quality.config import default_rules as d; from order_quality.reference import load_reference as l;"
            "from order_quality.validate import check_row as c; import pathlib, sys; r = d();"
            "v = dict(order_id='O', customer_id='C', order_date='2026-01-05', sku='SKU-001', quantity='0',"
            " amount_usd='0.00', channel='Web', status='Completed');"
            "print('|'.join(c(v, r, l(pathlib.Path(sys.argv[1]), r)).issues))")
    output = subprocess.run([sys.executable, "-O", "-c", code, str(REFERENCE)], capture_output=True, text=True, check=True)
    assert output.stdout.strip() == "INVALID_QUANTITY"


def test_a_missing_required_field_is_not_reported_twice():
    from dataclasses import replace
    rules = replace(RULES, required=("order_id", "customer_id", "sku"))
    assert check_row(raw(sku=""), rules, REF).issues == ("MISSING_SKU",)
    assert check_row(raw(sku=""), RULES, REF).issues == ("UNKNOWN_SKU",)


def test_quantity_limits_from_the_rules():
    from dataclasses import replace
    rules = replace(RULES, quantity_max=3)
    assert check_row(raw(quantity="4", amount_usd="50.00"), rules, REF).issues == ("INVALID_QUANTITY",)
