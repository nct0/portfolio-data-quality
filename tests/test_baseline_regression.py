"""The original 5,020-row scenario is a regression fixture: the default rules must reproduce its business results."""

from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal

from conftest import BASELINE_FILES, FIXTURES, HEADER, RULES_FILE, read_csv

from order_quality.config import default_rules, load_rules


def test_row_accounting_reproduces_the_baseline(baseline):
    rows = baseline.report["rows"]
    assert rows == {"input": 5020, "accepted": 4905, "rejected": 90, "duplicate_ignored": 15,
                    "quarantined_conflict": 10, "conflict_overridden": 0}
    assert rows["input"] == rows["accepted"] + rows["rejected"] + rows["duplicate_ignored"] + rows["quarantined_conflict"]


def test_revenue_matches_the_original_report(baseline):
    original = json.loads((FIXTURES / "quality_report.json").read_text(encoding="utf-8"))
    assert baseline.report["revenue"]["amount"] == original["completed_revenue_usd"] == "333886.85"
    assert baseline.report["revenue"]["orders"] == original["completed_order_count"] == 4170


def test_clean_orders_match_the_original_output(baseline):
    with (baseline.out / "clean_orders.csv").open(encoding="utf-8-sig", newline="") as handle:
        new = [row[:8] for row in csv.reader(handle)]
    with (FIXTURES / "clean_orders.csv").open(encoding="utf-8-sig", newline="") as handle:
        old = list(csv.reader(handle))
    assert new[0] == HEADER
    assert new == old


def test_monthly_revenue_is_byte_identical_to_the_original(baseline):
    assert (baseline.out / "monthly_revenue.csv").read_bytes() == (FIXTURES / "monthly_revenue.csv").read_bytes()


def test_rejected_rows_match_the_original_with_unique_headers(baseline):
    with (FIXTURES / "rejected_rows.csv").open(encoding="utf-8-sig", newline="") as handle:
        old_rows = list(csv.reader(handle))
    assert old_rows[0].count("order_id") == 2  # the original header repeated a column
    old = {(r[0], r[1]): (r[3], r[4:]) for r in old_rows[1:]}
    with (baseline.out / "rejected_rows.csv").open(encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    assert len(header) == len(set(header))
    new = {(r["source_file"], r["source_line"]): (r["issue_codes"], [r[f"raw_{c}"] for c in HEADER])
           for r in read_csv(baseline.out / "rejected_rows.csv")}
    assert new == old


def test_issue_counts_match_the_original(baseline):
    original = json.loads((FIXTURES / "quality_report.json").read_text(encoding="utf-8"))["issues"]
    expected = {k: v for k, v in original.items() if k not in ("EXACT_DUPLICATE", "CONFLICTING_DUPLICATE")}
    assert baseline.report["issues"] == expected


def test_duplicate_order_ids_match_the_original(baseline):
    old = read_csv(FIXTURES / "duplicates_review.csv")
    old_by_class = {c: {r["order_id"] for r in old if r["classification"] == c}
                    for c in ("EXACT_DUPLICATE", "CONFLICTING_DUPLICATE")}
    new = read_csv(baseline.out / "duplicates_review.csv")
    new_by_class = {c: {r["order_id"] for r in new if r["classification"] == c}
                    for c in ("EXACT_DUPLICATE", "CONFLICTING_DUPLICATE")}
    assert new_by_class == old_by_class
    assert len(new_by_class["EXACT_DUPLICATE"]) == 15 and len(new_by_class["CONFLICTING_DUPLICATE"]) == 5
    outcomes = Counter((r["classification"], r["outcome"]) for r in new)
    assert outcomes == {("EXACT_DUPLICATE", "ACCEPTED"): 15, ("EXACT_DUPLICATE", "DUPLICATE_IGNORED"): 15,
                        ("CONFLICTING_DUPLICATE", "QUARANTINED_CONFLICT"): 10}
    conflicts = {r["differing_fields"] for r in new if r["classification"] == "CONFLICTING_DUPLICATE"}
    assert conflicts == {"customer_id"}  # the generator changes the customer ID, not the amount


def test_every_reconciliation_check_passes(baseline):
    assert baseline.exit_code == 0
    assert baseline.report["status"] == "PASS"
    assert {c["check"] for c in baseline.report["checks"]} == {
        "row_accounting", "file_row_counts", "row_outcomes_complete", "clean_order_ids_unique",
        "clean_amounts_match_prices", "revenue_reconciles"}
    assert all(c["status"] == "PASS" for c in baseline.report["checks"])


def test_every_source_row_has_exactly_one_traceable_outcome(baseline):
    source = set()
    for path in BASELINE_FILES:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            next(reader)
            for _row in reader:
                source.add((path.name, str(reader.line_num)))
    outcomes = read_csv(baseline.out / "row_outcomes.csv")
    keys = [(r["source_file"], r["source_line"]) for r in outcomes]
    assert len(keys) == len(set(keys)) == 5020
    assert set(keys) == source
    assert Counter(r["outcome"] for r in outcomes) == {"ACCEPTED": 4905, "REJECTED": 90, "DUPLICATE_IGNORED": 15,
                                                      "QUARANTINED_CONFLICT": 10}


def test_clean_rows_point_back_to_their_source_line(baseline):
    lines = {}
    for path in BASELINE_FILES:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            next(reader)
            for row in reader:
                lines[(path.name, str(reader.line_num))] = row
    for row in read_csv(baseline.out / "clean_orders.csv")[:200]:
        raw = lines[(row["source_file"], row["source_line"])]
        assert raw[0].strip().upper() == row["order_id"]
        assert Decimal(raw[5]) == Decimal(row["amount_usd"])


def test_rules_file_states_exactly_the_built_in_rules():
    assert load_rules(RULES_FILE).sha256 == default_rules().sha256


def test_baseline_has_no_correction_suggestions(baseline):
    assert baseline.report["corrections"]["suggested"] == 0
