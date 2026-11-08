"""The Excel report: content equals the CSV outputs, totals are right, bytes are deterministic, input text stays text."""

from __future__ import annotations

import json
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import (BASELINE_FILES, DEMO_DECISIONS, LATE_BATCH, RULES_FILE, order, read_csv, run_pipeline,
                      write_orders)
from openpyxl import load_workbook

from order_quality import cli, workbook
from order_quality.errors import OutputError

# openpyxl reads the workbook back but does not know Excel's data-bar extension; Excel itself shows it.
pytestmark = pytest.mark.filterwarnings("ignore:.*extension is not supported:UserWarning")
ALL = [*BASELINE_FILES, LATE_BATCH / "orders_export_4.csv"]
SHEETS = ["Executive Dashboard", "Monthly Revenue", "Data Quality Summary", "Rejected Records", "Duplicate Review",
          "Correction Log", "Clean Orders", "Processing Audit"]


@pytest.fixture(scope="module")
def baseline_run(tmp_path_factory):
    return run_pipeline(tmp_path_factory.mktemp("wb-baseline") / "out", BASELINE_FILES, workbook=True)


@pytest.fixture(scope="module")
def late_run(tmp_path_factory):
    return run_pipeline(tmp_path_factory.mktemp("wb-late") / "out", ALL, corrections=DEMO_DECISIONS, workbook=True)


def report_path(result) -> Path:
    return result.out / workbook.WORKBOOK


def below(ws, label: str):
    """The value under the first cell whose text is `label` (dashboard tiles: label above value)."""
    for row in ws.iter_rows():
        for cell in row:
            if cell.value == label:
                return ws.cell(row=cell.row + 1, column=cell.column).value
    raise AssertionError(f"label {label!r} not found")


def row_with(ws, first_value: str) -> tuple:
    for row in ws.iter_rows(values_only=True):
        if row and row[0] == first_value:
            return row
    raise AssertionError(f"row {first_value!r} not found")


def worksheet_xml(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet")}


def test_sheets_in_order(baseline_run):
    assert report_path(baseline_run).is_file()
    assert load_workbook(report_path(baseline_run), read_only=True).sheetnames == SHEETS


@pytest.mark.parametrize("fixture", ["baseline_run", "late_run"])
def test_dashboard_tiles_equal_the_quality_report(fixture, request):
    result = request.getfixturevalue(fixture)
    report = json.loads((result.out / "quality_report.json").read_text(encoding="utf-8"))
    ws = load_workbook(report_path(result))["Executive Dashboard"]
    rows = report["rows"]
    assert below(ws, "Source rows") == rows["input"]
    assert below(ws, "Accepted orders") == rows["accepted"]
    assert below(ws, "Rejected rows") == rows["rejected"]
    assert below(ws, "Duplicates ignored") == rows["duplicate_ignored"]
    assert below(ws, "Quarantined rows") == rows["quarantined_conflict"]
    assert below(ws, "Completed orders") == report["revenue"]["orders"]
    assert Decimal(str(below(ws, "Completed revenue"))) == Decimal(report["revenue"]["amount"])
    assert "PASS" in str(ws["B4"].value) and report["status"] == "PASS"


def test_revenue_is_not_double_counted(baseline_run):
    """Baseline defect 12: the old dashboard summed the monthly rows and the TOTAL row together."""
    book = load_workbook(report_path(baseline_run))
    monthly = book["Monthly Revenue"]
    header = row_with(monthly, "Month")
    total = row_with(monthly, "Total")
    month_rows = [r for r in monthly.iter_rows(min_row=6, max_row=11, values_only=True)]
    csv_total = sum((Decimal(r["revenue_usd"]) for r in read_csv(baseline_run.out / "monthly_revenue.csv")),
                    Decimal("0.00"))
    grand = Decimal(str(total[header.index("Total")]))
    assert grand == csv_total == Decimal("333886.85")
    assert sum(Decimal(str(r[header.index("Total")])) for r in month_rows) == grand
    assert Decimal(str(below(book["Executive Dashboard"], "Completed revenue"))) == grand


def test_detail_sheets_match_the_csv_files(late_run):
    book = load_workbook(report_path(late_run), read_only=True)
    for sheet, name in (("Clean Orders", "clean_orders.csv"), ("Rejected Records", "rejected_rows.csv"),
                        ("Duplicate Review", "duplicates_review.csv"), ("Correction Log", "corrections_applied.csv")):
        rows = list(book[sheet].iter_rows(min_row=5, values_only=True))
        csv_rows = read_csv(late_run.out / name)
        assert len(rows) == len(csv_rows), sheet
        first = csv_rows[0]
        assert rows[0][0] == first[next(iter(first))], sheet
    clean = list(book["Clean Orders"].iter_rows(min_row=5, values_only=True))
    assert sum(Decimal(str(r[5])) for r in clean if r[7] == "Completed") == Decimal("352317.20")


def test_correction_log_shows_every_decision(late_run):
    ws = load_workbook(report_path(late_run), read_only=True)["Correction Log"]
    statuses = [r[9] for r in ws.iter_rows(min_row=5, values_only=True)]
    assert sorted(set(statuses)) == ["APPLIED", "PENDING", "REJECTED"]
    assert (statuses.count("APPLIED"), statuses.count("REJECTED"), statuses.count("PENDING")) == (14, 1, 2)


def test_processing_audit_lists_inputs_and_hashes(late_run):
    report = json.loads((late_run.out / "quality_report.json").read_text(encoding="utf-8"))
    values = {v for row in load_workbook(report_path(late_run), read_only=True)["Processing Audit"].iter_rows(
        values_only=True) for v in row if v is not None}
    for item in report["inputs"]:
        assert {item["file"], item["sha256"]} <= values
    assert {report["rules"]["sha256"], report["reference"]["sha256"]} <= values


def test_workbook_bytes_are_deterministic(tmp_path, baseline_run):
    first = report_path(baseline_run).read_bytes()
    workbook.build(baseline_run.out, 100_000)
    assert report_path(baseline_run).read_bytes() == first
    again = run_pipeline(tmp_path / "out", BASELINE_FILES, workbook=True)
    assert report_path(again).read_bytes() == first


def test_same_workbook_for_full_incremental_and_batch_sizes(tmp_path, late_run):
    expected = report_path(late_run).read_bytes()
    out = tmp_path / "incremental"
    for count in range(1, len(ALL) + 1):
        result = run_pipeline(out, ALL[:count], corrections=DEMO_DECISIONS, workbook=True)
    assert result.actions[-1]["action"] == "NEW"
    assert report_path(result).read_bytes() == expected
    batched = run_pipeline(tmp_path / "batched", ALL, corrections=DEMO_DECISIONS, workbook=True, batch_size=7)
    assert report_path(batched).read_bytes() == expected


def test_detail_sheets_are_capped_with_a_banner(tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text(RULES_FILE.read_text(encoding="utf-8").replace("max_detail_rows = 100000", "max_detail_rows = 50"),
                     encoding="utf-8")
    result = run_pipeline(tmp_path / "out", BASELINE_FILES, config=rules, workbook=True)
    ws = load_workbook(report_path(result), read_only=True)["Clean Orders"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[1][0].startswith("Showing the first 50 of 4,905 accepted orders")
    assert "clean_orders.csv" in rows[1][0]
    assert len(rows) == 4 + 50
    assert len(read_csv(result.out / "clean_orders.csv")) == 4905  # the CSV stays complete


def test_formula_like_input_is_stored_as_text(tmp_path):
    source = write_orders(tmp_path / "in" / "orders_export_1.csv", [
        order("ORD-1", customer="=1+1"),
        order("ORD-2", channel='=HYPERLINK("http://example.com","x")'),
        order("ORD-3", customer="@SUM(A1)", status="+cmd"),
    ])
    result = run_pipeline(tmp_path / "out", [source], workbook=True)
    path = report_path(result)
    assert all("<f>" not in xml for xml in worksheet_xml(path).values())  # no cell formulas anywhere
    book = load_workbook(path)
    clean = list(book["Clean Orders"].iter_rows(min_row=5, values_only=True))
    assert clean[0][1] == "'=1+1"
    rejected = list(book["Rejected Records"].iter_rows(min_row=5, values_only=True))
    assert {r[2] for r in rejected} == {"ORD-2", "ORD-3"}
    texts = {v for r in rejected for v in r if isinstance(v, str)}
    assert {"'=HYPERLINK(\"http://example.com\",\"x\")", "'@SUM(A1)", "'+cmd"} <= texts


def test_report_without_accepted_orders(tmp_path):
    source = write_orders(tmp_path / "in" / "orders_export_1.csv", [order("ORD-1", qty="0"), order("", sku="X")])
    result = run_pipeline(tmp_path / "out", [source], workbook=True)
    book = load_workbook(report_path(result))
    assert book["Clean Orders"]["A4"].value == "No accepted orders in this run."
    assert book["Monthly Revenue"]["A4"].value == "No accepted Completed orders in this run."
    assert book["Correction Log"]["A4"].value.startswith("No correction suggestions")
    assert below(book["Executive Dashboard"], "Accepted orders") == 0


def test_dashboard_shows_failed_checks(tmp_path, baseline_run):
    out = tmp_path / "out"
    out.mkdir()
    for f in baseline_run.out.iterdir():
        if f.suffix in (".csv", ".json"):
            (out / f.name).write_bytes(f.read_bytes())
    report = json.loads((out / "quality_report.json").read_text(encoding="utf-8"))
    report["status"] = "FAIL"
    report["checks"][0]["status"] = "FAIL"
    (out / "quality_report.json").write_text(json.dumps(report), encoding="utf-8")
    ws = load_workbook(workbook.build(out, 100))["Executive Dashboard"]
    assert str(ws["B4"].value).startswith("✖ FAIL   1 of 6 reconciliation checks failed")
    assert "✖ FAIL" in [c.value for row in ws.iter_rows() for c in row]


def test_cli_builds_the_workbook_unless_told_not_to(tmp_path, capsys):
    raw = str(BASELINE_FILES[0].parent)
    assert cli.main(["run", "--input", raw, "--out", str(tmp_path / "with"), "--quiet"]) == 0
    assert (tmp_path / "with" / workbook.WORKBOOK).is_file()
    assert cli.main(["run", "--input", raw, "--out", str(tmp_path / "without"), "--quiet", "--no-workbook"]) == 0
    assert not (tmp_path / "without" / workbook.WORKBOOK).exists()
    assert (tmp_path / "without" / "clean_orders.csv").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks files that are open in another program")
def test_open_workbook_is_reported_and_left_intact(tmp_path, baseline_run):
    out = tmp_path / "out"
    run_pipeline(out, BASELINE_FILES, workbook=True)
    before = (out / workbook.WORKBOOK).read_bytes()
    with (out / workbook.WORKBOOK).open("rb"):  # like Excel holding the file open
        with pytest.raises(OutputError) as error:
            workbook.build(out, 100_000)
    assert error.value.code == "OUT001"
    assert (out / workbook.WORKBOOK).read_bytes() == before
    assert not list(out.glob(".*.tmp"))
