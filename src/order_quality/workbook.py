"""Excel report for a non-technical reader, built only from the files the pipeline has written.

The workbook reads the CSV outputs and quality_report.json from the output folder, so it can never show a number
those files do not contain. Rows are streamed (XlsxWriter constant-memory mode keeps memory flat however large the
data is), detail sheets stop at `[workbook] max_detail_rows` with a banner that names the complete CSV file, and the
bytes are deterministic: the same outputs always give the same file.

Input text is written with `write_string`, never as a formula; the workbook contains no cell formulas at all.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import xlsxwriter
from xlsxwriter.worksheet import Worksheet

from order_quality import __version__
from order_quality import errors as codes
from order_quality import outputs
from order_quality.export import CSV_ENCODING, replace

WORKBOOK = "order_quality_report.xlsx"
EXCEL_MAX_ROWS = 1_048_576
FIRST_DATA_ROW = 4  # rows 0-2: title, banner, spacer; row 3: header
NO_DATA_DATE = datetime(2000, 1, 1)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# Colors: blue for revenue, orange for issues (categorical slots 1 and 2); status colors only for PASS/FAIL and review states.
INK, INK_2, MUTED = "#0B0B0B", "#52514E", "#898781"
HEADER, TILE, BAND, GRID, AXIS = "#184F95", "#F2F5FA", "#F3F6FB", "#E1E0D9", "#C3C2B7"
REVENUE, ISSUES = "#2A78D6", "#EB6834"
SERIES = ("#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#008300", "#6250D6", "#E34948")  # fixed order
GOOD_FILL, GOOD_INK = "#E3F4E3", "#006300"
BAD_FILL, BAD_INK = "#FBE3E3", "#B42318"
WARN_FILL, WARN_INK = "#FEF3D6", "#7A4F00"
NEUTRAL_FILL = "#EDEDEA"

OUTCOME_TEXT = {
    codes.ACCEPTED: ("Accepted", "Valid and unique; in the clean orders and the revenue report."),
    codes.REJECTED: ("Rejected", "Failed at least one rule; listed with its reasons, not used in totals."),
    codes.DUPLICATE_IGNORED: ("Duplicate ignored", "An exact copy of an accepted order; counted once."),
    codes.QUARANTINED_CONFLICT: ("Quarantined (conflict)", "One order ID with different versions; held back for review."),
    codes.CONFLICT_OVERRIDDEN: ("Conflict overridden", "A conflicting version replaced by the first one, as configured."),
}
CHECK_TEXT = {
    "row_accounting": "Every source row has exactly one outcome",
    "file_row_counts": "Rows taken from each file equal the rows read",
    "row_outcomes_complete": "The row-outcome file lists every source row",
    "clean_order_ids_unique": "Clean orders have unique order IDs",
    "clean_amounts_match_prices": "Clean amounts equal unit price x quantity",
    "revenue_reconciles": "Monthly revenue equals the clean-order detail",
}
NORMALIZATION_TEXT = {
    "whitespace_trimmed": "Extra spaces removed",
    "case_standardized": "Capitalization standardized",
    "uppercased": "Converted to upper case",
    "alias_mapped": "Approved alias replaced by the standard name",
    "date_to_iso": "Date converted to YYYY-MM-DD",
}
RULE_TEXT = {
    "sku_format": "SKU written without the standard format",
    "status_typo": "Likely spelling mistake in the status",
    "channel_typo": "Likely spelling mistake in the channel",
    "date_format": "Unambiguous date in a format that was not agreed",
}
REVIEW_STATUS_FILL = {"APPLIED": (GOOD_FILL, GOOD_INK), "REJECTED": (BAD_FILL, BAD_INK),
                      "PENDING": (WARN_FILL, WARN_INK), "STALE": (NEUTRAL_FILL, INK_2)}


def build(out: Path, max_detail_rows: int) -> Path:
    """Write `order_quality_report.xlsx` in `out` from the outputs already there. Returns its path."""
    path = out / WORKBOOK
    report = json.loads((out / outputs.QUALITY_REPORT).read_text(encoding="utf-8"))
    cap = max(1, min(max_detail_rows, EXCEL_MAX_ROWS - FIRST_DATA_ROW))
    fd, tmp = tempfile.mkstemp(dir=out, prefix=f".{WORKBOOK}.", suffix=".tmp")
    os.close(fd)
    try:
        _Report(Path(tmp), out, report, cap).write()
        replace(Path(tmp), path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


class _Report:
    def __init__(self, tmp: Path, out: Path, report: dict[str, Any], cap: int) -> None:
        self.out, self.report, self.cap = out, report, cap
        self.wb = xlsxwriter.Workbook(str(tmp), {
            "constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False,
            "strings_to_numbers": False})
        self.f = _formats(self.wb, report["revenue"]["currency"], Decimal(report["revenue"]["amount"]))
        self.latest_order = NO_DATA_DATE
        names = ("Executive Dashboard", "Monthly Revenue", "Data Quality Summary", "Rejected Records",
                 "Duplicate Review", "Correction Log", "Clean Orders", "Processing Audit")
        self.ws = {name: self.wb.add_worksheet(name) for name in names}

    def write(self) -> None:
        monthly = _read(self.out / outputs.MONTHLY)
        months, channels, revenue_cells = self._monthly(monthly)
        issue_cells = self._data_quality()
        self._rejected()
        self._duplicates()
        self._corrections()
        self._clean()
        self._audit()
        self._dashboard(months, channels, revenue_cells, issue_cells)
        self.wb.set_properties({
            "title": "Order data quality report",
            "subject": "Clean orders, rejected rows, duplicates and reconciled revenue",
            "comments": f"Generated by order_quality {__version__} from the pipeline outputs.",
            "created": self.latest_order,
        })
        self.wb.close()

    # Dashboard ---------------------------------------------------------------------------------------------

    def _dashboard(self, months: int, channels: list[str], revenue: dict[str, str], issues: dict[str, str]) -> None:
        ws, f, r = self.ws["Executive Dashboard"], self.f, self.report
        rows, revenue_info = r["rows"], r["revenue"]
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 2)
        ws.set_column(1, 8, 15)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        files = [i for i in r["inputs"] if i["status"] == "INCLUDED"]
        skipped = len(r["inputs"]) - len(files)
        ws.set_row(0, 30)
        ws.merge_range(0, 1, 0, 8, "Order data quality report", f["title"])
        source = (f"{len(files)} export file{'s' if len(files) != 1 else ''}"
                  + (f" ({skipped} skipped as duplicate content)" if skipped else "")
                  + f"  ·  rules: {r['rules']['source']}  ·  amounts in {revenue_info['currency']}"
                  + f"  ·  revenue counts {', '.join(revenue_info['statuses'])} orders")
        ws.merge_range(1, 1, 1, 8, source, f["subtitle"])

        checks = r["checks"]
        passed = sum(c["status"] == "PASS" for c in checks)
        ws.set_row(3, 30)
        if r["status"] == "PASS":
            status = (f"✔ PASS   All {len(checks)} reconciliation checks passed: every source row is accounted for "
                      "and the revenue totals agree.")
        else:
            status = (f"✖ FAIL   {len(checks) - passed} of {len(checks)} reconciliation checks failed. Do not use "
                      "these totals until the failed checks below are resolved.")
        ws.merge_range(3, 1, 3, 8, status, f["status_pass" if r["status"] == "PASS" else "status_fail"])

        total = rows["input"]
        share = (lambda n: f"{n / total:.1%} of source rows") if total else (lambda n: "")
        conflicting = r["duplicates"]["conflicting_order_ids"]
        tiles = [
            [("Source rows", rows["input"], "int", f"in {len(files)} file{'s' if len(files) != 1 else ''}"),
             ("Accepted orders", rows["accepted"], "int", share(rows["accepted"])),
             ("Rejected rows", rows["rejected"], "int", share(rows["rejected"]) + " · see Rejected Records"),
             ("Duplicates ignored", rows["duplicate_ignored"], "int", "exact copies counted once")],
            [("Quarantined rows", rows["quarantined_conflict"], "int",
              f"{conflicting} order ID{'s' if conflicting != 1 else ''} with conflicting versions"),
             ("Completed orders", revenue_info["orders"], "int", "orders counted in revenue"),
             (f"{', '.join(revenue_info['statuses'])} revenue", Decimal(revenue_info["amount"]), "money",
              "gross, before taxes and fees; equals the Monthly Revenue total")],
        ]
        row = 5
        for line in tiles:
            spans = [(1, 2), (3, 4), (5, 6), (7, 8)] if len(line) == 4 else [(1, 2), (3, 4), (5, 8)]
            ws.set_row(row + 1, 36)
            for offset, key in ((0, "tile_label"), (1, None), (2, "tile_note")):
                for (first, last), (label, value, kind, note) in zip(spans, line):
                    if key == "tile_label":
                        ws.merge_range(row, first, row, last, label, f["tile_label"])
                    elif key == "tile_note":
                        ws.merge_range(row + 2, first, row + 2, last, note, f["tile_note"])
                    else:
                        ws.merge_range(row + 1, first, row + 1, last, float(value),
                                       f["tile_money" if kind == "money" else "tile_int"])
            row += 4

        ws.write_string(row, 1, "Where every source row went", f["h2"])
        row += 1
        ws.merge_range(row, 1, row, 2, "Outcome", f["header"])
        ws.write_string(row, 3, "Rows", f["header_num"])
        ws.write_string(row, 4, "Share", f["header_num"])
        ws.merge_range(row, 5, row, 8, "What it means", f["header"])
        row += 1
        for outcome in codes.OUTCOMES:
            count = rows[outcome.lower()]
            if outcome == codes.CONFLICT_OVERRIDDEN and not count:
                continue
            label, meaning = OUTCOME_TEXT[outcome]
            ws.merge_range(row, 1, row, 2, label, f["cell"])
            ws.write_number(row, 3, count, f["int"])
            ws.write_number(row, 4, count / total if total else 0, f["pct"])
            ws.merge_range(row, 5, row, 8, meaning, f["cell_wrap"])
            row += 1
        ws.merge_range(row, 1, row, 2, "Source rows", f["total"])
        ws.write_number(row, 3, total, f["total_int"])
        ws.write_number(row, 4, 1 if total else 0, f["total_pct"])
        ws.merge_range(row, 5, row, 8, "the sum of the outcomes above", f["total"])
        row += 2

        ws.write_string(row, 1, "Reconciliation checks", f["h2"])
        row += 1
        ws.merge_range(row, 1, row, 3, "Check", f["header"])
        ws.write_string(row, 4, "Status", f["header"])
        ws.merge_range(row, 5, row, 8, "Detail", f["header"])
        row += 1
        for check in checks:
            ws.set_row(row, 30)
            ws.merge_range(row, 1, row, 3, CHECK_TEXT.get(check["check"], check["check"]), f["cell_wrap"])
            ws.write_string(row, 4, _status_label(check["status"]), f["pass" if check["status"] == "PASS" else "fail"])
            ws.merge_range(row, 5, row, 8, check["detail"], f["cell_wrap"])
            row += 1
        row += 1

        ws.write_string(row, 1, "What this means", f["h2"])
        row += 1
        policy = ("held back in full until someone decides which version is right"
                  if r["duplicates"]["policy"] == "quarantine_all"
                  else "resolved by keeping the first version, as configured; the others are listed for review")
        notes = [
            f"{rows['accepted']:,} orders are ready to use. The revenue figures are calculated from these orders only.",
            f"{rows['rejected']:,} rows need a fix at the source. Each one is listed with its file, line and reason.",
            f"Exact copies of an order are counted once. Orders that arrived in conflicting versions are {policy}.",
            "Nothing was deleted silently: every source row appears in exactly one of the outcomes above.",
        ]
        for note in notes:
            ws.set_row(row, 20)
            ws.merge_range(row, 1, row, 8, f"•  {note}", f["note_line"])
            row += 1
        row += 1

        statuses = ", ".join(revenue_info["statuses"])
        if months:
            chart = self.wb.add_chart({"type": "column"})
            chart.add_series({"name": f"{statuses} revenue", "categories": revenue["months"],
                              "values": revenue["month_totals"], "fill": {"color": REVENUE}, "border": {"none": True},
                              "gap": 60})
            _style_chart(chart, f"{statuses} revenue by month", f["axis_money"], categories_horizontal=True)
            chart.set_size({"width": 880, "height": 300})
            ws.insert_chart(row, 1, chart, {"x_offset": 0, "y_offset": 4})
            chart = self.wb.add_chart({"type": "bar"})
            chart.add_series({"name": "Revenue by channel", "categories": revenue["channels"],
                              "values": revenue["channel_totals"], "fill": {"color": REVENUE},
                              "border": {"none": True}, "gap": 60})
            _style_chart(chart, "Revenue by sales channel", f["axis_money"], categories_horizontal=False)
            chart.set_size({"width": 432, "height": 280})
            ws.insert_chart(row + 16, 1, chart, {"x_offset": 0, "y_offset": 4})
        if issues:
            chart = self.wb.add_chart({"type": "bar"})
            chart.add_series({"name": "Rejected rows", "categories": issues["codes"], "values": issues["counts"],
                              "fill": {"color": ISSUES}, "border": {"none": True}, "gap": 50})
            _style_chart(chart, "Rejected rows by reason", f["axis_int"], categories_horizontal=False)
            chart.set_size({"width": 432, "height": 280})
            ws.insert_chart(row + 16, 5, chart, {"x_offset": 8, "y_offset": 4})

    # Monthly revenue ---------------------------------------------------------------------------------------

    def _monthly(self, monthly: list[dict[str, str]]) -> tuple[int, list[str], dict[str, str]]:
        ws, f = self.ws["Monthly Revenue"], self.f
        revenue = self.report["revenue"]
        channels = sorted({m["channel"] for m in monthly})
        months = sorted({m["month"] for m in monthly})
        amount = {(m["month"], m["channel"]): Decimal(m["revenue_usd"]) for m in monthly}
        orders = {(m["month"], m["channel"]): int(m["completed_orders"]) for m in monthly}
        last = len(channels) + 1
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 14)
        ws.set_column(1, last, 16)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        statuses = ", ".join(revenue["statuses"])
        ws.set_row(0, 26)
        ws.write_string(0, 0, f"{statuses} revenue by month and sales channel", f["title"])
        ws.write_string(1, 0, f"Accepted orders with status {statuses}. Gross amounts in {revenue['currency']}, "
                              "before taxes and fees. Same data as monthly_revenue.csv.", f["subtitle"])
        cells: dict[str, str] = {}
        if not months:
            ws.write_string(3, 0, f"No accepted {statuses} orders in this run.", f["note"])
            return 0, [], cells
        row = 3
        blocks = (("Revenue", amount, f["money"], f["total_money"], Decimal("0.00")),
                  ("Completed orders", orders, f["int"], f["total_int"], 0))
        for block, (title, values, fmt, total_fmt, zero) in enumerate(blocks):
            ws.write_string(row, 0, title, f["h2"])
            row += 1
            ws.write_string(row, 0, "Month", f["header"])
            for col, channel in enumerate(channels, start=1):
                ws.write_string(row, col, channel, f["header_num"])
            ws.write_string(row, last, "Total", f["header_num"])
            header_row, row = row, row + 1
            first = row
            for month in months:
                ws.write_string(row, 0, _month_label(month), f["cell"])
                line_total = zero
                for col, channel in enumerate(channels, start=1):
                    value = values.get((month, channel), zero)
                    line_total += value
                    ws.write_number(row, col, float(value), fmt)
                ws.write_number(row, last, float(line_total), fmt)
                row += 1
            ws.write_string(row, 0, "Total", f["total"])
            grand = zero
            for col, channel in enumerate(channels, start=1):
                column_total = sum((values.get((m, channel), zero) for m in months), zero)
                grand += column_total
                ws.write_number(row, col, float(column_total), total_fmt)
            ws.write_number(row, last, float(grand), total_fmt)
            if block == 0 and len(channels) <= len(SERIES):  # more channels than colors: the table alone shows them
                self._channel_month_chart(ws, channels, first, row - 1, last)
                sheet = "'Monthly Revenue'"
                cells = {
                    "months": f"={sheet}!$A${first + 1}:$A${row}",
                    "month_totals": f"={sheet}!${_col(last)}${first + 1}:${_col(last)}${row}",
                    "channels": f"={sheet}!$B${header_row + 1}:${_col(last - 1)}${header_row + 1}",
                    "channel_totals": f"={sheet}!$B${row + 1}:${_col(last - 1)}${row + 1}",
                }
            row += 2
        check = next((c for c in self.report["checks"] if c["check"] == "revenue_reconciles"), None)
        if check:
            ws.write_string(row, 0, "Check", f["h2"])
            ws.set_row(row + 1, 30)
            ws.write_string(row + 1, 0, _status_label(check["status"]), f["pass" if check["status"] == "PASS" else "fail"])
            ws.merge_range(row + 1, 1, row + 1, last, f"{CHECK_TEXT['revenue_reconciles']}: {check['detail']}",
                           f["cell_wrap"])
        return len(months), channels, cells

    def _channel_month_chart(self, ws: Worksheet, channels: list[str], first: int, last_row: int, last: int) -> None:
        chart = self.wb.add_chart({"type": "column"})
        for col, channel in enumerate(channels, start=1):
            chart.add_series({"name": ["Monthly Revenue", first - 1, col], "values": ["Monthly Revenue", first, col, last_row, col],
                              "categories": ["Monthly Revenue", first, 0, last_row, 0],
                              "fill": {"color": SERIES[col - 1]}, "border": {"none": True}, "gap": 80, "overlap": -8})
        statuses = ", ".join(self.report["revenue"]["statuses"])
        _style_chart(chart, f"{statuses} revenue by month and channel", self.f["axis_money"], categories_horizontal=True)
        chart.set_legend({"position": "bottom", "font": {"name": "Calibri", "size": 9, "color": INK_2}})
        chart.set_size({"width": 620, "height": 320})
        ws.insert_chart(3, last + 2, chart, {"x_offset": 0, "y_offset": 0})

    # Data quality summary ----------------------------------------------------------------------------------

    def _data_quality(self) -> dict[str, str]:
        ws, f, r = self.ws["Data Quality Summary"], self.f, self.report
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 30)
        ws.set_column(1, 1, 80)
        ws.set_column(2, 2, 16)
        ws.set_column(3, 3, 24)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.set_row(0, 26)
        ws.write_string(0, 0, "Data quality summary", f["title"])
        ws.write_string(1, 0, "Why rows were rejected, how duplicates were handled, what was standardized "
                              "automatically and which corrections wait for approval.", f["subtitle"])
        row = 3
        rejected = r["rows"]["rejected"]
        issues = sorted(r["issues"].items(), key=lambda item: (-item[1], item[0]))
        ws.write_string(row, 0, "Rejected rows by reason", f["h2"])
        row += 1
        for col, text in enumerate(("Reason code", "What it means", "Rows", "Share of rejected rows")):
            ws.write_string(row, col, text, f["header_num" if col >= 2 else "header"])
        row += 1
        first = row
        for code, count in issues:
            ws.write_string(row, 0, code, f["code"])
            ws.write_string(row, 1, _issue_text(code), f["cell"])
            ws.write_number(row, 2, count, f["int"])
            ws.write_number(row, 3, count / rejected if rejected else 0, f["pct"])
            row += 1
        cells: dict[str, str] = {}
        if issues:
            ws.conditional_format(first, 2, row - 1, 2, {"type": "data_bar", "bar_color": ISSUES, "bar_solid": True,
                                                       "min_type": "num", "min_value": 0})
            sheet = "'Data Quality Summary'"
            cells = {"codes": f"={sheet}!$A${first + 1}:$A${row}", "counts": f"={sheet}!$C${first + 1}:$C${row}"}
            ws.write_string(row, 0, "A row can fail more than one rule, so the shares can add up to more than 100%.",
                            f["note"])
        else:
            ws.write_string(row, 0, "No rows were rejected.", f["note"])
        row += 2

        d = r["duplicates"]
        ws.write_string(row, 0, "Duplicates", f["h2"])
        row += 1
        ws.write_string(row, 0, "Measure", f["header"])
        ws.write_string(row, 1, "What it means", f["header"])
        ws.write_string(row, 2, "Count", f["header_num"])
        row += 1
        for label, meaning, value in (
            ("Exact duplicate order IDs", "Order IDs that arrived more than once with identical values", d["exact_duplicate_order_ids"]),
            ("Duplicate rows ignored", "The extra copies; each order is counted once", d["duplicate_rows_ignored"]),
            ("Conflicting order IDs", "Order IDs that arrived in two or more different versions", d["conflicting_order_ids"]),
            ("Rows quarantined", "Every version of a conflicting order ID, held back for review", d["quarantined_rows"]),
            ("Conflicts overridden", "Versions replaced by the first one (only with the keep_first policy)", d["overridden_rows"]),
        ):
            ws.write_string(row, 0, label, f["cell"])
            ws.write_string(row, 1, meaning, f["cell"])
            ws.write_number(row, 2, value, f["int"])
            row += 1
        ws.write_string(row, 0, "Conflict policy", f["cell"])
        ws.write_string(row, 1, "quarantine_all: no version of a conflicting order is used until it is resolved"
                        if d["policy"] == "quarantine_all" else
                        "keep_first: the first version (by file name and line) is used; the others are listed",
                        f["cell"])
        ws.write_string(row, 2, d["policy"], f["code_right"])
        row += 2

        ws.write_string(row, 0, "Standardized automatically (no approval needed)", f["h2"])
        row += 1
        ws.write_string(row, 0, "Field", f["header"])
        ws.write_string(row, 1, "Change", f["header"])
        ws.write_string(row, 2, "Values changed", f["header_num"])
        row += 1
        normalizations = [(field, kind, count) for field, kinds in sorted(r["normalizations"].items())
                          for kind, count in sorted(kinds.items())]
        for field, kind, count in normalizations:
            ws.write_string(row, 0, field, f["code"])
            ws.write_string(row, 1, NORMALIZATION_TEXT.get(kind, kind), f["cell"])
            ws.write_number(row, 2, count, f["int"])
            row += 1
        if not normalizations:
            ws.write_string(row, 0, "No values needed standardizing.", f["note"])
            row += 1
        ws.write_string(row, 0, "Counted over every source row. Financial values, customer and order IDs are never "
                                "changed automatically.", f["note"])
        row += 2

        c = r["corrections"]
        ws.write_string(row, 0, "Suggested corrections (applied only after approval)", f["h2"])
        row += 1
        ws.write_string(row, 0, "Status", f["header"])
        ws.write_string(row, 1, "What it means", f["header"])
        ws.write_string(row, 2, "Suggestions", f["header_num"])
        row += 1
        for label, meaning, value in (
            ("Suggested", "Rejected values with one safe, unambiguous fix", c["suggested"]),
            ("Applied", "Approved by a reviewer and applied on this run", c["applied"]),
            ("Rejected by reviewer", "The reviewer decided against the fix; the row stays rejected", c["rejected"]),
            ("Pending review", "Waiting for a decision; the row stays rejected", c["pending"]),
            ("Stale decisions", "Decisions whose row no longer matches the input; not applied", c["stale"]),
        ):
            ws.write_string(row, 0, label, f["cell"])
            ws.write_string(row, 1, meaning, f["cell"])
            ws.write_number(row, 2, value, f["int"])
            row += 1
        for rule, count in c["by_rule"].items():
            ws.write_string(row, 0, f"Rule: {rule}", f["code"])
            ws.write_string(row, 1, RULE_TEXT.get(rule, rule), f["cell"])
            ws.write_number(row, 2, count, f["int"])
            row += 1
        ws.write_string(row, 0, "Details per suggestion are on the Correction Log sheet.", f["note"])
        return cells

    # Detail sheets -----------------------------------------------------------------------------------------

    def _rejected(self) -> None:
        columns = [("Source file", 20, "text"), ("Line", 8, "int"), ("Order ID", 14, "text"), ("Reasons", 30, "text"),
                   *((f"Received: {name}", width, "text") for name, width in (
                       ("order ID", 16), ("customer ID", 14), ("order date", 14), ("SKU", 13), ("quantity", 11),
                       ("amount", 12), ("channel", 14), ("status", 13))),
                   ("Extra fields", 16, "text")]
        self._detail("Rejected Records", "Rejected records", outputs.REJECTED, columns, "rejected rows",
                     "Values are shown exactly as received. Text that Excel could run as a formula starts with an "
                     "apostrophe.", freeze_cols=3)

    def _duplicates(self) -> None:
        columns = [("Order ID", 14, "text"), ("Classification", 24, "text"), ("Outcome", 24, "text"),
                   ("Occurrences", 12, "int"), ("Versions", 10, "int"), ("Version", 9, "int"),
                   ("Differing fields", 18, "text"), ("Source file", 20, "text"), ("Line", 8, "int"),
                   ("Customer ID", 13, "text"), ("Order date", 12, "date"), ("SKU", 11, "text"),
                   ("Quantity", 10, "int"), ("Amount", 12, "money"), ("Channel", 13, "text"), ("Status", 12, "text")]
        ws = self._detail("Duplicate Review", "Duplicate review", outputs.DUPLICATES, columns, "rows of repeated order IDs",
                          "One row per occurrence. Conflicting versions are highlighted; the differing values are "
                          "marked in orange.", freeze_cols=1)
        if ws is None:
            return
        last = FIRST_DATA_ROW + self.shown - 1
        f = self.f
        for outcome, fmt in ((codes.ACCEPTED, "cf_good"), (codes.DUPLICATE_IGNORED, "cf_neutral"),
                             (codes.QUARANTINED_CONFLICT, "cf_warn"), (codes.CONFLICT_OVERRIDDEN, "cf_warn")):
            ws.conditional_format(FIRST_DATA_ROW, 2, last, 2, {"type": "cell", "criteria": "==",
                                                               "value": f'"{outcome}"', "format": f[fmt]})
        for col, field in ((9, "customer_id"), (10, "order_date"), (11, "sku"), (12, "quantity"), (13, "amount_usd"),
                           (14, "channel"), (15, "status")):
            ws.conditional_format(FIRST_DATA_ROW, col, last, col, {
                "type": "formula", "criteria": f'=ISNUMBER(SEARCH("|{field}|","|"&$G{FIRST_DATA_ROW + 1}&"|"))',
                "format": f["cf_diff"]})

    def _corrections(self) -> None:
        reviewers = {row["correction_id"]: (row["reviewer"], row["note"])
                     for row in _read(self.out / outputs.CORRECTION_REVIEW)}
        columns = [("Correction ID", 21, "text"), ("Source file", 20, "text"), ("Line", 8, "int"), ("Order ID", 13, "text"),
                   ("Field", 11, "text"), ("Rule", 14, "text"), ("Received value", 16, "text"),
                   ("Suggested value", 16, "text"), ("Decision", 10, "text"), ("Status", 11, "text"),
                   ("Final value", 16, "text"), ("Row outcome", 22, "text"), ("Reviewer", 14, "text"), ("Note", 40, "text")]

        def rows() -> Iterator[list[str]]:
            for row in _read_rows(self.out / outputs.CORRECTIONS_APPLIED):
                yield [*row, *reviewers.get(row[0], ("", ""))]

        ws = self._detail("Correction Log", "Correction log", outputs.CORRECTIONS_APPLIED, columns, "suggestions",
                          "Suggestions are applied only after a reviewer writes APPROVE in correction_review.csv and "
                          "the pipeline runs again with --corrections. Rejected and pending suggestions change nothing.",
                          freeze_cols=1, rows=rows(), empty="No correction suggestions in this run: no rejected value "
                          "had a single safe fix.")
        if ws is None:
            return
        last = FIRST_DATA_ROW + self.shown - 1
        for status, (fill, ink) in REVIEW_STATUS_FILL.items():
            ws.conditional_format(FIRST_DATA_ROW, 9, last, 9, {"type": "cell", "criteria": "==", "value": f'"{status}"',
                                                               "format": self.f[f"cf_{status.lower()}"]})

    def _clean(self) -> None:
        columns = [("Order ID", 14, "text"), ("Customer ID", 13, "text"), ("Order date", 12, "date"),
                   ("SKU", 11, "text"), ("Quantity", 10, "int"), ("Amount", 12, "money"), ("Channel", 13, "text"),
                   ("Status", 12, "text"), ("Source file", 20, "text"), ("Line", 8, "int")]

        def rows() -> Iterator[list[str]]:
            for row in _read_rows(self.out / outputs.CLEAN):
                day = _date(row[2])
                if day and day > self.latest_order:
                    self.latest_order = day
                yield row

        self._detail("Clean Orders", "Clean orders", outputs.CLEAN, columns, "accepted orders, sorted by order ID",
                     "Validated, deduplicated orders: the data behind every total in this report.", freeze_cols=1,
                     rows=rows())

    def _detail(self, sheet: str, title: str, source: str, columns: list[tuple[str, int, str]], noun: str,
                note: str, freeze_cols: int, rows: Iterator[list[str]] | None = None,
                empty: str | None = None) -> Worksheet | None:
        ws, f = self.ws[sheet], self.f
        total = _count(self.out / source)
        self.shown = shown = min(total, self.cap)
        for col, (header, width, _) in enumerate(columns):
            ws.set_column(col, col, max(width, len(header) + 4))  # room for the filter button
        ws.hide_gridlines(2)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.set_row(0, 26)
        ws.write_string(0, 0, title, f["title"])
        if total > shown:
            ws.write_string(1, 0, f"Showing the first {shown:,} of {total:,} {noun}. The complete list is in {source}. "
                                  f"{note}", f["banner"])
        else:
            ws.write_string(1, 0, f"All {total:,} {noun}. Same data as {source}. {note}", f["subtitle"])
        if not total:
            ws.write_string(3, 0, empty or f"No {noun.split(',')[0]} in this run.", f["note"])
            return None
        last_col = len(columns) - 1
        for col, (header, _, _) in enumerate(columns):
            ws.write_string(3, col, header, f["header"])  # left-aligned: the filter button sits at the right edge
        writers = [self._writer(kind) for _, _, kind in columns]
        row = FIRST_DATA_ROW
        for values in rows if rows is not None else _read_rows(self.out / source):
            if row - FIRST_DATA_ROW >= shown:
                if rows is None:
                    break
                continue  # keep consuming a generator that collects facts about every row
            for col, (write, value) in enumerate(zip(writers, values)):
                write(ws, row, col, value)
            row += 1
        last = FIRST_DATA_ROW + shown - 1
        ws.autofilter(3, 0, last, last_col)
        ws.freeze_panes(FIRST_DATA_ROW, freeze_cols)
        ws.repeat_rows(3)
        ws.conditional_format(FIRST_DATA_ROW, 0, last, last_col, {"type": "formula", "criteria": "=MOD(ROW(),2)=0",
                                                                  "format": f["band"]})
        return ws

    def _writer(self, kind: str):
        f = self.f

        def text(ws: Worksheet, row: int, col: int, value: str) -> None:
            if value:
                ws.write_string(row, col, value, f["cell"])

        def integer(ws: Worksheet, row: int, col: int, value: str) -> None:
            if value.isascii() and value.isdigit():
                ws.write_number(row, col, int(value), f["int"])
            else:
                text(ws, row, col, value)

        def money(ws: Worksheet, row: int, col: int, value: str) -> None:
            try:
                ws.write_number(row, col, float(Decimal(value)), f["money"])
            except ArithmeticError:
                text(ws, row, col, value)

        def day(ws: Worksheet, row: int, col: int, value: str) -> None:
            parsed = _date(value)
            if parsed:
                ws.write_datetime(row, col, parsed, f["date"])
            else:
                text(ws, row, col, value)

        return {"text": text, "int": integer, "money": money, "date": day}[kind]

    # Processing audit --------------------------------------------------------------------------------------

    def _audit(self) -> None:
        ws, f, r = self.ws["Processing Audit"], self.f, self.report
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 30)
        ws.set_column(1, 1, 30)
        ws.set_column(2, 3, 12)
        ws.set_column(4, 4, 70)
        ws.set_column(5, 5, 22)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.set_row(0, 26)
        ws.write_string(0, 0, "Processing audit", f["title"])
        ws.write_string(1, 0, "The inputs and rules behind this report. Per-run file actions (new, unchanged, "
                              "restaged) are logged in processing_audit.csv and run_log.jsonl.", f["subtitle"])
        row = 3
        ws.write_string(row, 0, "Input files", f["h2"])
        row += 1
        for col, text in enumerate(("File", "Status", "Data rows", "Bytes", "SHA-256 (content fingerprint)",
                                    "Same content as")):
            ws.write_string(row, col, text, f["header_num" if col in (2, 3) else "header"])
        row += 1
        for item in r["inputs"]:
            ws.write_string(row, 0, item["file"], f["cell"])
            ws.write_string(row, 1, "Included" if item["status"] == "INCLUDED" else "Skipped: duplicate content",
                            f["cell"])
            ws.write_number(row, 2, item["data_rows"], f["int"])
            ws.write_number(row, 3, item["bytes"], f["int"])
            ws.write_string(row, 4, item["sha256"], f["mono"])
            if item["duplicate_of"]:
                ws.write_string(row, 5, item["duplicate_of"], f["cell"])
            row += 1
        ws.write_string(row, 0, "Source rows processed", f["total"])
        ws.write_string(row, 1, "", f["total"])
        ws.write_number(row, 2, r["rows"]["input"], f["total_int"])
        row += 3

        rules, reference, revenue = r["rules"], r["reference"], r["revenue"]
        ws.write_string(row, 0, "Rules and reference data", f["h2"])
        row += 1
        ws.write_string(row, 0, "Setting", f["header"])
        ws.merge_range(row, 1, row, 4, "Value", f["header"])
        row += 1
        for label, value in (
            ("Rules", rules["source"]),
            ("Rules SHA-256", rules["sha256"]),
            ("Accepted date formats", ", ".join(rules["date_formats"])),
            ("Amount tolerance", f"{rules['amount_tolerance']} {revenue['currency']}"),
            ("Duplicate conflict policy", rules["conflict_policy"]),
            ("Revenue statuses", ", ".join(revenue["statuses"])),
            ("Reference data SHA-256", reference["sha256"]),
            ("Products in the price list", f"{reference['products']:,}"),
            ("Sales channels", ", ".join(reference["channels"])),
            ("Rows per detail sheet (at most)", f"{self.cap:,}"),
            ("Report generated by", f"order_quality {__version__}"),
        ):
            ws.write_string(row, 0, label, f["cell"])
            ws.merge_range(row, 1, row, 4, value, f["mono"] if "SHA-256" in label else f["cell"])
            row += 1
        row += 2

        ws.write_string(row, 0, "Reconciliation checks", f["h2"])
        row += 1
        ws.write_string(row, 0, "Check", f["header"])
        ws.write_string(row, 1, "Status", f["header"])
        ws.merge_range(row, 2, row, 5, "Detail", f["header"])
        row += 1
        for check in r["checks"]:
            ws.write_string(row, 0, CHECK_TEXT.get(check["check"], check["check"]), f["cell"])
            ws.write_string(row, 1, _status_label(check["status"]), f["pass" if check["status"] == "PASS" else "fail"])
            ws.merge_range(row, 2, row, 5, check["detail"], f["cell"])
            row += 1


# Helpers -----------------------------------------------------------------------------------------------------

def _formats(wb: xlsxwriter.Workbook, currency: str, revenue: Decimal) -> dict[str, Any]:
    money = "$#,##0.00" if currency == "USD" else f'#,##0.00 "{currency}"'
    thousands = ',"K"' if revenue >= 10_000 else ""  # short axis labels ($40K) so they never overlap
    axis_money = f"$#,##0{thousands}" if currency == "USD" else f'#,##0{thousands} "{currency}"'
    base = {"font_name": "Calibri", "font_size": 10, "font_color": INK, "valign": "vcenter"}

    def fmt(**props: Any):
        return wb.add_format({**base, **props})

    edge = {"bottom": 1, "bottom_color": GRID}
    return {
        "title": fmt(bold=True, font_size=16),
        "subtitle": fmt(font_color=INK_2),
        "h2": fmt(bold=True, font_size=12, font_color=HEADER),
        "header": fmt(bold=True, font_color="#FFFFFF", bg_color=HEADER, text_wrap=True, border=1, border_color=HEADER),
        "header_num": fmt(bold=True, font_color="#FFFFFF", bg_color=HEADER, text_wrap=True, align="right", border=1,
                          border_color=HEADER),
        "cell": fmt(**edge),
        "cell_wrap": fmt(text_wrap=True, **edge),
        "code": fmt(font_name="Consolas", **edge),
        "code_right": fmt(font_name="Consolas", align="right", **edge),
        "mono": fmt(font_name="Consolas", font_size=9, **edge),
        "int": fmt(num_format="#,##0", **edge),
        "money": fmt(num_format=money, **edge),
        "pct": fmt(num_format="0.0%", **edge),
        "date": fmt(num_format="yyyy-mm-dd", align="left", **edge),
        "total": fmt(bold=True, top=1, top_color=INK, bottom=1, bottom_color=INK),
        "total_int": fmt(bold=True, num_format="#,##0", top=1, top_color=INK, bottom=1, bottom_color=INK),
        "total_money": fmt(bold=True, num_format=money, top=1, top_color=INK, bottom=1, bottom_color=INK),
        "total_pct": fmt(bold=True, num_format="0.0%", top=1, top_color=INK, bottom=1, bottom_color=INK),
        "note": fmt(italic=True, font_color=INK_2),
        "note_line": fmt(font_size=11, text_wrap=True),
        "banner": fmt(bold=True, font_color=WARN_INK, bg_color=WARN_FILL),
        "pass": fmt(bold=True, font_color=GOOD_INK, bg_color=GOOD_FILL, align="center", **edge),
        "fail": fmt(bold=True, font_color=BAD_INK, bg_color=BAD_FILL, align="center", **edge),
        "status_pass": fmt(bold=True, font_size=12, font_color=GOOD_INK, bg_color=GOOD_FILL, indent=1),
        "status_fail": fmt(bold=True, font_size=12, font_color=BAD_INK, bg_color=BAD_FILL, indent=1),
        "tile_label": fmt(font_color=INK_2, bg_color=TILE, indent=1, top=2, top_color=HEADER),
        "tile_int": fmt(bold=True, font_size=22, num_format="#,##0", bg_color=TILE, align="left", indent=1),
        "tile_money": fmt(bold=True, font_size=22, num_format=money, bg_color=TILE, align="left", indent=1),
        "tile_note": fmt(font_size=9, font_color=INK_2, bg_color=TILE, indent=1),
        "band": wb.add_format({"bg_color": BAND}),
        "cf_good": wb.add_format({"bg_color": GOOD_FILL, "font_color": GOOD_INK}),
        "cf_neutral": wb.add_format({"bg_color": NEUTRAL_FILL, "font_color": INK_2}),
        "cf_warn": wb.add_format({"bg_color": WARN_FILL, "font_color": WARN_INK, "bold": True}),
        "cf_diff": wb.add_format({"bg_color": "#FBE1D4", "font_color": "#8A3412", "bold": True}),
        "cf_applied": wb.add_format({"bg_color": GOOD_FILL, "font_color": GOOD_INK, "bold": True}),
        "cf_rejected": wb.add_format({"bg_color": BAD_FILL, "font_color": BAD_INK, "bold": True}),
        "cf_pending": wb.add_format({"bg_color": WARN_FILL, "font_color": WARN_INK, "bold": True}),
        "cf_stale": wb.add_format({"bg_color": NEUTRAL_FILL, "font_color": INK_2}),
        "axis_money": axis_money,
        "axis_int": "#,##0",
    }


def _style_chart(chart, title: str, value_format: str, categories_horizontal: bool) -> None:
    font = {"name": "Calibri", "color": INK_2, "size": 9}
    chart.set_title({"name": title, "name_font": {"name": "Calibri", "size": 12, "bold": True, "color": INK},
                     "overlay": False})
    chart.set_legend({"none": True})
    values = {"num_format": value_format, "num_font": font, "line": {"none": True}, "min": 0,
              "major_gridlines": {"visible": True, "line": {"color": GRID, "width": 0.75}}}
    categories = {"num_font": font, "line": {"color": AXIS}, "major_tick_mark": "none"}
    if categories_horizontal:
        chart.set_x_axis(categories)
        chart.set_y_axis(values)
    else:  # bar chart: categories on the vertical axis, first category at the top
        chart.set_y_axis({**categories, "reverse": True, "crossing": "max"})
        chart.set_x_axis(values)
    chart.set_chartarea({"border": {"none": True}, "fill": {"color": "#FFFFFF"}})
    chart.set_plotarea({"border": {"none": True}})


def _status_label(status: str) -> str:
    return "✔ PASS" if status == "PASS" else "✖ FAIL"


def _issue_text(code: str) -> str:
    if code in codes.ISSUE_CODES:
        return codes.ISSUE_CODES[code]
    if code.startswith(codes.MISSING_PREFIX):
        return f"The required field {code[len(codes.MISSING_PREFIX):].lower()} is empty."
    return code


def _month_label(month: str) -> str:
    year, _, number = month.partition("-")
    return f"{_MONTHS[int(number) - 1]} {year}" if number.isdigit() and 1 <= int(number) <= 12 else month


def _date(value: str) -> datetime | None:
    try:
        return datetime.combine(date.fromisoformat(value), datetime.min.time())
    except ValueError:
        return None


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding=CSV_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def _read_rows(path: Path) -> Iterator[list[str]]:
    with path.open(encoding=CSV_ENCODING, newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        yield from reader


def _count(path: Path) -> int:
    return sum(1 for _ in _read_rows(path))
