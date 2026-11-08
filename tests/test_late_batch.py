"""Day 2: a late export re-sends, contradicts and misspells earlier orders across batches."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal

import pytest
from conftest import BASELINE_FILES, DEMO_DECISIONS, LATE_BATCH, read_csv, run_pipeline

MANIFEST = json.loads((LATE_BATCH / "manifest.json").read_text(encoding="utf-8"))
INPUTS = [*BASELINE_FILES, LATE_BATCH / "orders_export_4.csv"]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("late")
    return {"none": run_pipeline(folder / "none", INPUTS),
            "demo": run_pipeline(folder / "demo", INPUTS, corrections=DEMO_DECISIONS)}


def test_results_without_decisions_match_the_manifest(baseline, runs):
    effect = MANIFEST["effect_on_baseline"]["without_decisions"]
    rows, base = runs["none"].report["rows"], baseline.report["rows"]
    for key in ("input", "accepted", "rejected", "duplicate_ignored", "quarantined_conflict"):
        assert rows[key] == base[key] + effect[key], key
    revenue = runs["none"].report["revenue"]
    assert revenue["orders"] == baseline.report["revenue"]["orders"] + effect["revenue_orders"]
    assert Decimal(revenue["amount"]) == Decimal(baseline.report["revenue"]["amount"]) + Decimal(effect["revenue_usd"])


def test_results_with_demo_decisions_match_the_manifest(baseline, runs):
    effect = MANIFEST["effect_on_baseline"]["with_demo_decisions"]
    rows, base = runs["demo"].report["rows"], baseline.report["rows"]
    assert rows["accepted"] == base["accepted"] + effect["accepted"]
    assert rows["rejected"] == base["rejected"] + effect["rejected"]
    revenue = runs["demo"].report["revenue"]
    assert revenue["orders"] == baseline.report["revenue"]["orders"] + effect["revenue_orders"]
    assert Decimal(revenue["amount"]) == Decimal(baseline.report["revenue"]["amount"]) + Decimal(effect["revenue_usd"])
    assert runs["demo"].report["corrections"] == {"suggested": 17, "applied": 14, "rejected": 1, "pending": 2, "stale": 0,
                                                  "by_rule": MANIFEST["suggestions"]}


def test_a_late_conflicting_resend_quarantines_a_previously_accepted_order(baseline, runs):
    before = {r["order_id"] for r in read_csv(baseline.out / "clean_orders.csv")}
    after = {r["order_id"] for r in read_csv(runs["none"].out / "clean_orders.csv")}
    assert {"ORD-000600", "ORD-000601"} <= before
    assert not {"ORD-000600", "ORD-000601"} & after
    review = [r for r in read_csv(runs["none"].out / "duplicates_review.csv") if r["order_id"] == "ORD-000600"]
    assert len(review) == 2 and review[0]["source_file"] < review[1]["source_file"] == "orders_export_4.csv"
    assert {r["outcome"] for r in review} == {"QUARANTINED_CONFLICT"}
    assert review[0]["differing_fields"] == "quantity|amount_usd"


def test_a_third_conflicting_version_stays_quarantined(runs):
    review = [r for r in read_csv(runs["none"].out / "duplicates_review.csv") if r["order_id"] == "ORD-000116"]
    assert len(review) == 3
    assert {r["versions"] for r in review} == {"3"}
    assert sorted(r["version"] for r in review) == ["1", "2", "3"]
    assert {r["outcome"] for r in review} == {"QUARANTINED_CONFLICT"}


def test_exact_resends_across_batches_are_ignored_once(runs):
    review = read_csv(runs["none"].out / "duplicates_review.csv")
    resent = [r for r in review if r["order_id"] in {"ORD-000500", "ORD-000501", "ORD-000502", "ORD-000503", "ORD-000504"}]
    assert Counter(r["outcome"] for r in resent) == {"ACCEPTED": 5, "DUPLICATE_IGNORED": 5}
    assert all(r["source_file"] == "orders_export_4.csv" for r in resent if r["outcome"] == "DUPLICATE_IGNORED")


def test_correction_audit_records_original_suggested_and_final_values(runs):
    audit = read_csv(runs["demo"].out / "corrections_applied.csv")
    assert Counter(r["status"] for r in audit) == {"APPLIED": 14, "REJECTED": 1, "PENDING": 2}
    for row in audit:
        if row["status"] == "APPLIED":
            assert row["final_value"] == row["suggested_value"] and row["row_outcome"] == "ACCEPTED"
        else:
            assert row["final_value"] == row["original_value"] and row["row_outcome"] == "REJECTED"
    outcomes = {(r["source_file"], r["source_line"]): r for r in read_csv(runs["demo"].out / "row_outcomes.csv")}
    applied = [r for r in audit if r["status"] == "APPLIED"]
    assert all(outcomes[(r["source_file"], r["source_line"])]["corrected_fields"] == r["field"] for r in applied)


def test_unapproved_suggestions_change_nothing(runs):
    review = read_csv(runs["none"].out / "correction_review.csv")
    assert len(review) == 17 and all(r["decision"] == "" for r in review)
    rejected = {(r["source_file"], r["source_line"]) for r in read_csv(runs["none"].out / "rejected_rows.csv")}
    assert all((r["source_file"], r["source_line"]) in rejected for r in review)
    assert {r["field"] for r in review} == {"sku", "status", "channel", "order_date"}
