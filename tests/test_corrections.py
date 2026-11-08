"""Assisted correction: suggestions need an explicit decision; decisions are audited and reproducible."""

from __future__ import annotations

import csv

import pytest
from conftest import REFERENCE, RULES_FILE, order, read_csv, run_pipeline, write_orders

from order_quality.config import default_rules, load_rules
from order_quality.corrections import REVIEW_COLUMNS, Suggester, correction_id, levenshtein
from order_quality.errors import InputError
from order_quality.reference import load_reference
from order_quality.validate import check_row

RULES = default_rules()
REF = load_reference(REFERENCE, RULES)
FIELDS = ("order_id", "customer_id", "order_date", "sku", "quantity", "amount_usd", "channel", "status")


def suggest(rules=RULES, **overrides):
    raw = dict(zip(FIELDS, order()))
    raw.update(overrides)
    row = check_row(raw, rules, REF)
    return {(s.field, s.rule): s.suggested for s in Suggester(rules, REF).suggest("in.csv", 2, raw, row)}


@pytest.mark.parametrize("value, expected", [("SKU007", "SKU-007"), ("sku 003", "SKU-003"), ("SKU_005", "SKU-005"),
                                             ("SKU.008", "SKU-008")])
def test_sku_format_variants_are_suggested(value, expected):
    assert suggest(sku=value) == {("sku", "sku_format"): expected}


def test_typos_and_alternate_dates_are_suggested():
    assert suggest(status="Complted") == {("status", "status_typo"): "Completed"}
    assert suggest(channel="Marketplce") == {("channel", "channel_typo"): "Marketplace"}
    assert suggest(order_date="2026/07/14") == {("order_date", "date_format"): "2026-07-14"}
    assert suggest(order_date="Jul 21, 2026") == {("order_date", "date_format"): "2026-07-21"}


@pytest.mark.parametrize("overrides", [{"sku": "SKU-INVALID"}, {"sku": "SKU-999"}, {"status": "MISSING"},
                                       {"status": "Shipped"}, {"channel": "UNKNOWN"}, {"channel": "Phone"},
                                       {"order_date": "not-a-date"}, {"order_date": "07/03/26"}])
def test_no_suggestion_without_a_unique_candidate(overrides):
    assert suggest(**overrides) == {}


def test_ambiguous_typo_gets_no_suggestion(tmp_path):
    path = tmp_path / "rules.toml"
    path.write_text(RULES_FILE.read_text(encoding="utf-8").replace(
        'allowed = ["Completed", "Refunded", "Pending"]', 'allowed = ["Completed", "Refunded", "Pending", "Paid", "Said"]'),
        encoding="utf-8")
    assert suggest(load_rules(path), status="Xaid") == {}
    assert suggest(load_rules(path), status="Paidd") == {("status", "status_typo"): "Paid"}


def test_ids_quantities_amounts_and_customers_are_never_suggested():
    found = suggest(order_id="", customer_id="", quantity="2.0", amount_usd="12,50", sku="SKU007", status="Complted")
    assert {field for field, _rule in found} == {"sku", "status"}


def test_levenshtein():
    assert levenshtein("pendng", "pending") == 1 and levenshtein("abc", "abc") == 0 and levenshtein("", "ab") == 2


def decisions_file(path, review_rows, decisions):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in review_rows:
            writer.writerow({**row, "decision": decisions.get(row["original_value"], "")})
    return path


def test_approve_reject_and_pending(tmp_path):
    inputs = [write_orders(tmp_path / "in.csv", [order("ORD-1", sku="SKU007", amount="44.00"),
                                                 order("ORD-2", status="Pendng"), order("ORD-3", channel="Retal")])]
    first = run_pipeline(tmp_path / "out", inputs)
    assert first.report["rows"]["rejected"] == 3
    review = read_csv(tmp_path / "out" / "correction_review.csv")
    path = decisions_file(tmp_path / "decisions.csv", review, {"SKU007": "approve", "Pendng": "REJECT"})
    second = run_pipeline(tmp_path / "out", inputs, corrections=path)
    assert second.report["rows"]["accepted"] == 1 and second.report["rows"]["rejected"] == 2
    clean = read_csv(tmp_path / "out" / "clean_orders.csv")
    assert [(r["order_id"], r["sku"]) for r in clean] == [("ORD-1", "SKU-007")]
    audit = {r["original_value"]: r for r in read_csv(tmp_path / "out" / "corrections_applied.csv")}
    assert audit["SKU007"]["status"] == "APPLIED" and audit["SKU007"]["final_value"] == "SKU-007"
    assert audit["Pendng"]["status"] == "REJECTED" and audit["Pendng"]["final_value"] == "Pendng"
    assert audit["Retal"]["status"] == "PENDING" and audit["Retal"]["row_outcome"] == "REJECTED"
    assert {a["action"] for a in second.actions} == {"RESTAGED"}


def test_approved_correction_does_not_hide_another_issue(tmp_path):
    inputs = [write_orders(tmp_path / "in.csv", [order("ORD-1", sku="SKU007", amount="10.00")])]
    run_pipeline(tmp_path / "out", inputs)
    review = read_csv(tmp_path / "out" / "correction_review.csv")
    result = run_pipeline(tmp_path / "out", inputs,
                          corrections=decisions_file(tmp_path / "d.csv", review, {"SKU007": "APPROVE"}))
    assert result.report["issues"] == {"AMOUNT_MISMATCH": 1}
    assert read_csv(tmp_path / "out" / "corrections_applied.csv")[0]["row_outcome"] == "REJECTED"


def test_decision_for_a_changed_row_is_stale_and_not_applied(tmp_path):
    path = write_orders(tmp_path / "in.csv", [order("ORD-1", sku="SKU007", amount="44.00")])
    run_pipeline(tmp_path / "out", [path])
    decisions = decisions_file(tmp_path / "d.csv", read_csv(tmp_path / "out" / "correction_review.csv"), {"SKU007": "APPROVE"})
    write_orders(path, [order("ORD-1", sku="SKU 007", amount="44.00")])  # the client re-sent a different value
    result = run_pipeline(tmp_path / "out", [path], corrections=decisions)
    assert result.report["corrections"]["stale"] == 1 and result.report["corrections"]["applied"] == 0
    assert result.report["rows"]["accepted"] == 0
    stale = [r for r in read_csv(tmp_path / "out" / "corrections_applied.csv") if r["status"] == "STALE"]
    assert len(stale) == 1 and stale[0]["decision"] == "APPROVE"


def test_decision_file_errors_are_actionable(tmp_path):
    inputs = [write_orders(tmp_path / "in.csv", [order("ORD-1")])]
    cid = correction_id("in.csv", 2, "sku", "SKU007", "SKU-007")
    cases = {
        "missing": None,
        "bad decision": f"correction_id,source_file,decision\n{cid},in.csv,yes\n",
        "bad id": "correction_id,source_file,decision\n12345,in.csv,APPROVE\n",
        "both": f"correction_id,source_file,decision\n{cid},in.csv,APPROVE\n{cid},in.csv,REJECT\n",
        "columns": "id,decision\nx,APPROVE\n",
    }
    for name, text in cases.items():
        path = tmp_path / f"{name}.csv"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        with pytest.raises(InputError) as error:
            run_pipeline(tmp_path / "out", inputs, corrections=path)
        assert error.value.code == "IN006", name


def test_correction_ids_are_stable():
    assert correction_id("a.csv", 3, "sku", "SKU007", "SKU-007") == correction_id("a.csv", 3, "sku", "SKU007", "SKU-007")
    assert correction_id("a.csv", 3, "sku", "SKU007", "SKU-007") != correction_id("a.csv", 4, "sku", "SKU007", "SKU-007")
    assert correction_id("a.csv", 3, "sku", "SKU007", "SKU-007").startswith("CR-")
