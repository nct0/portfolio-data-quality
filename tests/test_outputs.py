"""Output safety: formula injection, atomic replacement, encoding, determinism and failing checks."""

from __future__ import annotations

import csv
import json

import pytest
from conftest import BASELINE_FILES, order, run_pipeline, write_orders

from order_quality import export, outputs
from order_quality.errors import OutputError

CSV_OUTPUTS = (outputs.CLEAN, outputs.REJECTED, outputs.DUPLICATES, outputs.MONTHLY, outputs.ROW_OUTCOMES,
               outputs.CORRECTION_REVIEW, outputs.CORRECTIONS_APPLIED, outputs.PROCESSING_AUDIT)
DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def test_formula_like_values_are_neutralized_in_every_csv(tmp_path):
    rows = [order("ORD-1", customer="=HYPERLINK(\"http://example.com\",\"x\")"), order("=CMD|' /C calc'!A0"),
            order("ORD-3", customer="@SUM(1+1)", amount="+1"), order("-ORD-4", qty="-2"), order("ORD-5", channel="\t=1")]
    run_pipeline(tmp_path / "out", [write_orders(tmp_path / "in.csv", rows)])
    seen = 0
    for name in CSV_OUTPUTS:
        with (tmp_path / "out" / name).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                for cell in row:
                    assert not cell.startswith(DANGEROUS), (name, cell)
                    seen += cell.startswith("'")
    assert seen >= 5
    clean = list(csv.DictReader((tmp_path / "out" / outputs.CLEAN).open(encoding="utf-8-sig", newline="")))
    by_id = {r["order_id"]: r for r in clean}
    assert by_id["ORD-1"]["customer_id"].startswith("'=HYPERLINK")
    assert "'=CMD|' /C CALC'!A0" in by_id


def test_guard_leaves_numbers_and_plain_text_alone():
    assert export.guard(5) == 5 and export.guard("C-1") == "C-1" and export.guard("-1") == "'-1"


def test_failed_write_keeps_the_previous_file(tmp_path):
    target = tmp_path / "report.csv"
    export.write_csv(target, ["a"], [[1]])
    before = target.read_bytes()

    def rows():
        yield [2]
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        export.write_csv(target, ["a"], rows())
    assert target.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["report.csv"]


def test_locked_output_reports_out001(tmp_path, monkeypatch):
    target = tmp_path / "report.csv"
    export.write_csv(target, ["a"], [[1]])
    attempts = []

    def locked(src, dst):
        attempts.append(dst)
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(export.os, "replace", locked)
    monkeypatch.setattr(export.time, "sleep", lambda seconds: None)
    with pytest.raises(OutputError) as error:
        export.write_csv(target, ["a"], [[2]])
    assert error.value.code == "OUT001" and "open in another program" in error.value.message
    assert len(attempts) == len(export.REPLACE_RETRY_DELAYS) + 1
    assert [p.name for p in tmp_path.iterdir()] == ["report.csv"]


def test_a_briefly_held_output_is_replaced_after_a_retry(tmp_path, monkeypatch):
    # A virus scanner or the search indexer on Windows can hold a just-written file for a moment.
    target = tmp_path / "report.csv"
    export.write_csv(target, ["a"], [[1]])
    real_replace, refusals, sleeps = export.os.replace, [PermissionError(5, "Access is denied")] * 2, []

    def held_twice(src, dst):
        if refusals:
            raise refusals.pop()
        real_replace(src, dst)

    monkeypatch.setattr(export.os, "replace", held_twice)
    monkeypatch.setattr(export.time, "sleep", sleeps.append)
    export.write_csv(target, ["a"], [[2]])
    assert target.read_text(encoding="utf-8-sig").splitlines() == ["a", "2"]
    assert sleeps == list(export.REPLACE_RETRY_DELAYS[:2])
    assert [p.name for p in tmp_path.iterdir()] == ["report.csv"]


def test_csv_outputs_are_excel_friendly(baseline):
    data = (baseline.out / outputs.CLEAN).read_bytes()
    assert data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data


def test_business_outputs_contain_no_timestamps_or_local_paths(baseline):
    text = (baseline.out / outputs.QUALITY_REPORT).read_text(encoding="utf-8")
    report = json.loads(text)
    assert str(baseline.out.parent) not in text and str(BASELINE_FILES[0].parent) not in text
    assert "started" not in text and "T00:" not in text
    assert [i["file"] for i in report["inputs"]] == [f.name for f in BASELINE_FILES]


def test_run_log_and_processing_audit_record_each_run(tmp_path):
    out = tmp_path / "out"
    run_pipeline(out, BASELINE_FILES)
    run_pipeline(out, BASELINE_FILES)
    log = [json.loads(line) for line in (out / outputs.RUN_LOG).read_text(encoding="utf-8").splitlines()]
    assert [e["run_id"] for e in log] == [1, 2]
    assert log[0]["files"] == {"NEW": 3} and log[1]["files"] == {"UNCHANGED": 3}
    assert {"stage", "resolve", "export", "checks", "total"} <= set(log[1]["seconds"])
    audit = list(csv.DictReader((out / outputs.PROCESSING_AUDIT).open(encoding="utf-8-sig", newline="")))
    assert {r["run_id"] for r in audit} == {"2"} and {r["action"] for r in audit} == {"UNCHANGED"}


def test_a_failing_reconciliation_check_exits_with_code_3(tmp_path, monkeypatch):
    original = outputs.write_clean

    def drop_last_row(state, out):
        count = original(state, out)
        path = out / outputs.CLEAN
        lines = path.read_bytes().split(b"\r\n")
        path.write_bytes(b"\r\n".join(lines[:-2] + [b""]))
        return count - 1

    monkeypatch.setattr(outputs, "write_clean", drop_last_row)
    result = run_pipeline(tmp_path / "out", [write_orders(tmp_path / "in.csv", [order("ORD-1"), order("ORD-2")])])
    assert result.exit_code == 3 and result.report["status"] == "FAIL"
    failed = {c["check"] for c in result.report["checks"] if c["status"] == "FAIL"}
    assert failed == {"clean_order_ids_unique", "revenue_reconciles"}
