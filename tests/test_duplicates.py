"""Duplicate resolution must not depend on file order, batch split or row order."""

from __future__ import annotations

import random
import textwrap
from collections import Counter

import pytest
from conftest import BASELINE_FILES, RULES_FILE, order, read_csv, run_pipeline, write_orders


def outcomes(out):
    return Counter((r["order_id"], r["outcome"]) for r in read_csv(out / "row_outcomes.csv"))


def clean_business(out):
    return [{k: v for k, v in r.items() if not k.startswith("source_")} for r in read_csv(out / "clean_orders.csv")]


def test_exact_copy_in_another_file_is_ignored_once(tmp_path):
    a = write_orders(tmp_path / "a.csv", [order("ORD-1", channel="Web")])
    b = write_orders(tmp_path / "b.csv", [order("ORD-1", channel=" web ", sku="sku-001")])
    result = run_pipeline(tmp_path / "out", [a, b])
    assert outcomes(tmp_path / "out") == {("ORD-1", "ACCEPTED"): 1, ("ORD-1", "DUPLICATE_IGNORED"): 1}
    clean = read_csv(tmp_path / "out" / "clean_orders.csv")
    assert clean[0]["source_file"] == "a.csv"  # the first occurrence by file name and line is kept
    assert result.report["duplicates"]["exact_duplicate_order_ids"] == 1


def test_conflict_across_files_quarantines_every_version(tmp_path):
    a = write_orders(tmp_path / "a.csv", [order("ORD-1", qty="2"), order("ORD-2")])
    b = write_orders(tmp_path / "b.csv", [order("ORD-1", qty="3")])
    result = run_pipeline(tmp_path / "out", [a, b])
    assert outcomes(tmp_path / "out")[("ORD-1", "QUARANTINED_CONFLICT")] == 2
    assert [r["order_id"] for r in read_csv(tmp_path / "out" / "clean_orders.csv")] == ["ORD-2"]
    review = read_csv(tmp_path / "out" / "duplicates_review.csv")
    assert {r["differing_fields"] for r in review} == {"quantity|amount_usd"}
    assert result.report["revenue"]["orders"] == 1


def test_repeated_conflicts_with_an_exact_copy_quarantine_all_rows(tmp_path):
    a = write_orders(tmp_path / "a.csv", [order("ORD-1", qty="1"), order("ORD-1", qty="1")])
    b = write_orders(tmp_path / "b.csv", [order("ORD-1", qty="2")])
    c = write_orders(tmp_path / "c.csv", [order("ORD-1", customer="C-2", qty="1")])
    run_pipeline(tmp_path / "out", [a, b, c])
    review = read_csv(tmp_path / "out" / "duplicates_review.csv")
    assert len(review) == 4 and {r["versions"] for r in review} == {"3"}
    assert [r["version"] for r in review] == ["1", "1", "2", "3"]
    assert {r["outcome"] for r in review} == {"QUARANTINED_CONFLICT"}
    assert review[0]["differing_fields"] == "customer_id|quantity|amount_usd"


@pytest.mark.parametrize("arrangement", [["A", "A", "B"], ["A", "B", "A"], ["B", "A", "A"]])
def test_classification_is_order_independent(tmp_path, arrangement):
    """The original counted a copy as exact or conflicting depending on whether the conflict came first."""
    versions = {"A": order("ORD-1", qty="1"), "B": order("ORD-1", qty="2")}
    channels = iter(["Web", " web ", "WEB"])  # same values, different files (identical files would be skipped)
    files = [write_orders(tmp_path / f"f{i}.csv", [versions[v][:6] + [next(channels), "Completed"]])
             for i, v in enumerate(arrangement)]
    result = run_pipeline(tmp_path / "out", files)
    assert result.report["rows"]["quarantined_conflict"] == 3
    assert result.report["rows"]["duplicate_ignored"] == 0
    assert {r["classification"] for r in read_csv(tmp_path / "out" / "duplicates_review.csv")} == {"CONFLICTING_DUPLICATE"}


def test_shuffling_rows_across_files_gives_the_same_business_results(tmp_path, baseline):
    rows = []
    for path in BASELINE_FILES:
        rows.extend(path.read_text(encoding="utf-8-sig").splitlines()[1:])
    random.Random(3).shuffle(rows)
    header = BASELINE_FILES[0].read_text(encoding="utf-8-sig").splitlines()[0]
    files = []
    for i in range(4):
        path = tmp_path / "shuffled" / f"part_{i}.csv"
        path.parent.mkdir(exist_ok=True)
        path.write_text("\n".join([header, *rows[i::4]]) + "\n", encoding="utf-8")
        files.append(path)
    result = run_pipeline(tmp_path / "out", files)
    assert result.report["rows"] == baseline.report["rows"]
    assert result.report["revenue"] == baseline.report["revenue"]
    assert result.report["issues"] == baseline.report["issues"]
    assert clean_business(tmp_path / "out") == clean_business(baseline.out)
    assert (tmp_path / "out" / "monthly_revenue.csv").read_bytes() == (baseline.out / "monthly_revenue.csv").read_bytes()


def test_a_rejected_row_never_blocks_or_replaces_a_valid_order(tmp_path):
    a = write_orders(tmp_path / "a.csv", [order("ORD-1")])
    b = write_orders(tmp_path / "b.csv", [order("ORD-1", amount="n/a")])
    run_pipeline(tmp_path / "out", [a, b])
    assert outcomes(tmp_path / "out") == {("ORD-1", "ACCEPTED"): 1, ("ORD-1", "REJECTED"): 1}


def test_keep_first_policy_is_explicit_and_reported(tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text(RULES_FILE.read_text(encoding="utf-8").replace('conflict_policy = "quarantine_all"',
                                                                    'conflict_policy = "keep_first"'), encoding="utf-8")
    a = write_orders(tmp_path / "a.csv", [order("ORD-1", qty="1")])
    b = write_orders(tmp_path / "b.csv", [order("ORD-1", qty="1"), order("ORD-1", qty="4")])
    result = run_pipeline(tmp_path / "out", [a, b], config=rules)
    assert outcomes(tmp_path / "out") == {("ORD-1", "ACCEPTED"): 1, ("ORD-1", "DUPLICATE_IGNORED"): 1,
                                         ("ORD-1", "CONFLICT_OVERRIDDEN"): 1}
    assert result.report["status"] == "PASS"
    assert result.report["duplicates"]["overridden_rows"] == 1
    assert read_csv(tmp_path / "out" / "clean_orders.csv")[0]["quantity"] == "1"
    assert "first version" in textwrap.shorten(" ".join(result.report["notes"]), 500)
