"""The Excel report: content equals the CSV outputs, totals are right, bytes are deterministic, input text stays text."""

from __future__ import annotations

import json
import re
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
SHEETS = ["Executive Dashboard", "Monthly Revenue", "Data Quality Summary", "Reconciliation Audit", "Rejected Records",
          "Duplicate Review", "Correction Log", "Clean Orders"]


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


def cells_with(ws, prefix: str) -> list[str]:
    return [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith(prefix)]


def chart_references(path: Path) -> dict[str, tuple[list[str], list[str]]]:
    """{chart title: (category ranges, value ranges)} read from the chart parts of the workbook."""
    charts = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if name.startswith("xl/charts/chart"):
                xml = archive.read(name).decode("utf-8")
                title = "".join(re.findall(r"<a:t>([^<]*)</a:t>", xml.split("</c:title>")[0]))
                charts[title] = (re.findall(r"<c:cat>.*?<c:f>([^<]+)</c:f>", xml, re.S),
                                 re.findall(r"<c:val>.*?<c:f>([^<]+)</c:f>", xml, re.S))
    return charts


def resolve(book, reference: str) -> list:
    sheet, cells = reference.rsplit("!", 1)
    found = book[sheet.strip("'")][cells.replace("$", "")]
    return [c.value for row in found for c in row]


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
    assert below(ws, "Source Records") == rows["input"]
    assert below(ws, "Accepted Orders") == rows["accepted"]
    assert Decimal(str(below(ws, "Completed Revenue"))) == Decimal(report["revenue"]["amount"])
    assert below(ws, "Reconciliation Status") == "✔ PASS" and report["status"] == "PASS"
    assert below(ws, "Rejected Rows") == rows["rejected"]
    assert below(ws, "Duplicates Ignored") == rows["duplicate_ignored"]
    assert below(ws, "Quarantined Rows") == rows["quarantined_conflict"]
    assert cells_with(ws, f"{report['revenue']['orders']:,} completed orders")
    assert cells_with(ws, "6 of 6 checks passed")
    assert len(cells_with(ws, "✔  ")) == 3  # the three check-summary lines


def test_revenue_is_not_double_counted(baseline_run):
    """Baseline defect 12: the old dashboard summed the monthly rows and the TOTAL row together."""
    book = load_workbook(report_path(baseline_run))
    monthly = book["Monthly Revenue"]
    rows = list(monthly.iter_rows(values_only=True))
    header_at = next(i for i, r in enumerate(rows) if r[0] == "Month")
    total_at = next(i for i, r in enumerate(rows) if i > header_at and r[0] == "Total")
    header, total, month_rows = rows[header_at], rows[total_at], rows[header_at + 1:total_at]
    csv_total = sum((Decimal(r["revenue_usd"]) for r in read_csv(baseline_run.out / "monthly_revenue.csv")),
                    Decimal("0.00"))
    grand = Decimal(str(total[header.index("Total")]))
    assert grand == csv_total == Decimal("333886.85")
    assert sum(Decimal(str(r[header.index("Total")])) for r in month_rows) == grand
    assert Decimal(str(below(book["Executive Dashboard"], "Completed Revenue"))) == grand


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


def test_reconciliation_audit_holds_row_accounting_checks_inputs_and_hashes(late_run):
    report = json.loads((late_run.out / "quality_report.json").read_text(encoding="utf-8"))
    ws = load_workbook(report_path(late_run))["Reconciliation Audit"]
    values = {v for row in ws.iter_rows(values_only=True) for v in row if v is not None}
    for item in report["inputs"]:
        assert {item["file"], item["sha256"]} <= values
    assert {report["rules"]["sha256"], report["reference"]["sha256"]} <= values
    assert {check["detail"] for check in report["checks"]} <= values
    assert row_with(ws, "Source rows")[1] == report["rows"]["input"] == 5293
    for label, key in (("Accepted", "accepted"), ("Rejected", "rejected"), ("Duplicate ignored", "duplicate_ignored"),
                       ("Quarantined (conflict)", "quarantined_conflict")):
        assert row_with(ws, label)[1] == report["rows"][key]


@pytest.mark.parametrize("fixture", ["baseline_run", "late_run"])
def test_dashboard_charts_read_the_reported_data(fixture, request):
    result = request.getfixturevalue(fixture)
    path = report_path(result)
    report = json.loads((result.out / "quality_report.json").read_text(encoding="utf-8"))
    monthly = read_csv(result.out / "monthly_revenue.csv")
    book = load_workbook(path)
    charts = chart_references(path)

    (categories,), (values,) = charts["Monthly Completed Revenue"]
    months = sorted({m["month"] for m in monthly})
    assert resolve(book, categories) == [workbook._month_label(m) for m in months]
    per_month = [sum((Decimal(m["revenue_usd"]) for m in monthly if m["month"] == month), Decimal("0.00"))
                 for month in months]
    assert [Decimal(str(v)).quantize(Decimal("0.01")) for v in resolve(book, values)] == per_month
    assert sum(per_month) == Decimal(report["revenue"]["amount"])

    (categories,), (values,) = charts["Revenue by Sales Channel"]
    channels = sorted({m["channel"] for m in monthly})
    assert resolve(book, categories) == channels
    assert [Decimal(str(v)).quantize(Decimal("0.01")) for v in resolve(book, values)] == [
        sum((Decimal(m["revenue_usd"]) for m in monthly if m["channel"] == c), Decimal("0.00")) for c in channels]

    (categories,), (values,) = charts["Data Quality Issues"]
    issues = sorted(report["issues"].items(), key=lambda item: (-item[1], item[0]))
    assert resolve(book, categories) == [workbook._issue_label(code) for code, _ in issues]
    assert resolve(book, values) == [count for _, count in issues]


def test_dashboard_fits_one_screen_without_overlapping_charts(baseline_run):
    path = report_path(baseline_run)
    ws = load_workbook(path)["Executive Dashboard"]
    last_col, last_row = 17, workbook.FOOTER_ROW + 1  # columns A..Q, rows 1..footer (1-based)
    width = sum(round((ws.column_dimensions[workbook._col(i)].width or 8.43) * 7) for i in range(last_col))
    height = sum((ws.row_dimensions[r].height or 15) * 96 / 72 for r in range(1, last_row + 1))
    assert width <= 1200 and height <= 600, (width, height)  # one laptop screen at 100% zoom
    assert ws.row_dimensions[workbook.KPI_ROW].height == 8.25  # spacer rows keep their height (1-based: row above)
    assert ws.row_dimensions[workbook.KPI_ROW + 1].height == 3  # the cards' accent row
    assert ws.max_row <= last_row and ws.max_column <= last_col
    with zipfile.ZipFile(path) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        drawing = archive.read("xl/drawings/drawing1.xml").decode("utf-8")
        names = archive.read("xl/workbook.xml").decode("utf-8")
    assert 'showGridLines="0"' in sheet and 'orientation="landscape"' in sheet and 'fitToPage="1"' in sheet
    assert "'Executive Dashboard'!$A$1:$Q$32" in names  # print area
    anchors = []
    for anchor in re.findall(r"<xdr:twoCellAnchor.*?</xdr:twoCellAnchor>", drawing, re.S):
        ends = [tuple(int(v) for v in re.findall(r">(\d+)<", part))
                for part in re.findall(r"<xdr:(?:from|to)>(.*?)</xdr:(?:from|to)>", anchor, re.S)]
        (col0, off0, row0, _), (col1, off1, row1, _) = ends
        anchors.append(((col0, off0), (col1, off1), row0, row1))
    assert len(anchors) == 3 and drawing.count('descr="') == 3  # each chart has alt text for screen readers
    anchors.sort()
    for (_, end, _, _), (start, _, _, _) in zip(anchors, anchors[1:]):
        assert end <= start  # side by side, never overlapping
    assert all(workbook.CHART_ROW <= row0 and row1 < workbook.FOOTER_ROW for _, _, row0, row1 in anchors)


def test_dashboard_check_summary_covers_every_check(baseline_run):
    grouped = sorted(name for _, names in workbook.CHECK_GROUPS for name in names)
    assert grouped == sorted(workbook.CHECK_TEXT) == sorted(c["check"] for c in baseline_run.report["checks"])


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
    dashboard = book["Executive Dashboard"]
    assert below(dashboard, "Accepted Orders") == 0
    assert cells_with(dashboard, "No accepted Completed orders in this run")
    assert list(chart_references(report_path(result))) == ["Data Quality Issues"]


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
    book = load_workbook(workbook.build(out, 100))
    ws = book["Executive Dashboard"]
    assert below(ws, "Reconciliation Status") == "✖ FAIL"
    assert cells_with(ws, "5 of 6 checks passed · see Reconciliation Audit")
    assert cells_with(ws, "✖  Every source row has one outcome") and len(cells_with(ws, "✔  ")) == 2
    audit = book["Reconciliation Audit"]
    assert audit["A4"].value.startswith("✖ FAIL   1 of 6 reconciliation checks failed")
    assert "✖ FAIL" in [c.value for row in audit.iter_rows() for c in row]


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
