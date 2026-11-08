"""Excel report for a non-technical reader, built only from the files the pipeline has written.

The workbook reads the CSV outputs and quality_report.json from the output folder, so it can never show a number
those files do not contain. Rows are streamed (XlsxWriter constant-memory mode keeps memory flat however large the
data is), detail sheets stop at `[workbook] max_detail_rows` with a banner that names the complete CSV file, and the
bytes are deterministic: the same outputs always give the same file.

Constant-memory mode writes each sheet row by row, so every merged cell spans a single row (dashboard cards are
stacked rows), and Excel table objects are not available: detail sheets use filters, frozen and repeated headers and
banded rows instead.

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
DASHBOARD, MONTHLY, QUALITY, AUDIT = "Executive Dashboard", "Monthly Revenue", "Data Quality Summary", "Reconciliation Audit"
SHEETS = (DASHBOARD, MONTHLY, QUALITY, AUDIT, "Rejected Records", "Duplicate Review", "Correction Log", "Clean Orders")

# Palette: navy for structure and revenue, teal for status and channels, neutrals for text and surfaces. Amber marks
# data-quality exceptions only; red appears only when a check fails.
NAVY, TEAL, AMBER, SLATE = "#1F2A44", "#0F766E", "#B7791F", "#64748B"
INK, INK_2, MUTED = "#1F2937", "#4B5563", "#6B7280"
SURFACE, BAND, RULE, AXIS = "#F3F5F8", "#F6F8FA", "#E2E6EC", "#C9CED6"
GOOD_FILL, GOOD_INK = "#E2F3F0", "#0B5F58"
BAD_FILL, BAD_INK = "#FDECEA", "#B42318"
WARN_FILL, WARN_INK = "#FCF1DC", "#7A4B00"
NEUTRAL_FILL = "#ECEEF1"
REVENUE, CHANNELS, ISSUES = "#1F3A5F", TEAL, AMBER
DATA_BAR = "#EBCB91"  # a light amber, so the count in the cell stays readable
SERIES = ("#1F3A5F", "#0F766E", "#8FA3BF", "#5EB8AE", "#B7791F", "#4B5563", "#C9A227", "#7C3A5E")  # fixed order

# Dashboard grid: four card slots of three columns, separated by narrow gap columns, about 1,160 x 570 pixels.
SLOTS, SLOT_COLS, SLOT_WIDTH, GAP_WIDTH = 4, 3, 12, 2
KPI_ROW, CHECKS_ROW, CHART_ROW, CHART_ROWS, FOOTER_ROW = 4, 10, 17, 13, 31

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
# The dashboard summarizes the six checks in three plain-language lines; the audit sheet lists each one.
CHECK_GROUPS = (
    ("Every source row has one outcome", ("row_accounting", "file_row_counts", "row_outcomes_complete")),
    ("Order IDs unique, amounts match prices", ("clean_order_ids_unique", "clean_amounts_match_prices")),
    ("Revenue matches the order detail", ("revenue_reconciles",)),
)
ISSUE_LABEL = {
    codes.AMOUNT_MISMATCH: "Amount ≠ price × qty",
    codes.INVALID_AMOUNT: "Invalid amount",
    codes.INVALID_DATE: "Invalid date",
    codes.INVALID_QUANTITY: "Invalid quantity",
    codes.UNKNOWN_CHANNEL: "Unknown channel",
    codes.UNKNOWN_SKU: "Unknown SKU",
    codes.UNKNOWN_STATUS: "Unknown status",
    codes.MALFORMED_ROW: "Malformed row",
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
        self.f = _formats(self.wb, report["revenue"]["currency"])
        self.latest_order = NO_DATA_DATE
        self.ws = {name: self.wb.add_worksheet(name) for name in SHEETS}

    def write(self) -> None:
        monthly = _read(self.out / outputs.MONTHLY)
        months, revenue_cells = self._monthly(monthly)
        issue_cells = self._data_quality()
        self._audit()
        self._rejected()
        self._duplicates()
        self._corrections()
        self._clean()
        self._dashboard(months, revenue_cells, issue_cells)
        self.wb.set_properties({
            "title": "Order data quality report",
            "subject": "Clean orders, rejected rows, duplicates and reconciled revenue",
            "comments": f"Generated by order_quality {__version__} from the pipeline outputs.",
            "created": self.latest_order,
        })
        self.wb.close()

    # Dashboard ---------------------------------------------------------------------------------------------

    def _dashboard(self, months: list[str], revenue: dict[str, str], issues: dict[str, str]) -> None:
        """One screen: title, four KPI cards, three data-quality indicators with a check summary, three charts."""
        ws, f, r = self.ws[DASHBOARD], self.f, self.report
        rows, revenue_info, checks = r["rows"], r["revenue"], r["checks"]
        statuses = ", ".join(revenue_info["statuses"])
        files = [i for i in r["inputs"] if i["status"] == "INCLUDED"]
        skipped = len(r["inputs"]) - len(files)
        total = rows["input"]
        ok = r["status"] == "PASS"
        passed = sum(c["status"] == "PASS" for c in checks)
        slots = [_slot(i) for i in range(SLOTS)]
        last_col = slots[-1][1] + 1

        ws.hide_gridlines(2)
        ws.set_column(0, 0, GAP_WIDTH)
        for first, last in slots:
            ws.set_column(first, last, SLOT_WIDTH)
            ws.set_column(last + 1, last + 1, GAP_WIDTH)  # the gap after the last slot is the right margin
        ws.set_landscape()
        ws.fit_to_pages(1, 1)
        ws.center_horizontally()
        ws.set_margins(left=0.4, right=0.4, top=0.5, bottom=0.5)
        ws.print_area(0, 0, FOOTER_ROW, last_col)

        def line(row: int, height: float, cells: list[tuple[int, int, Any, str]]) -> None:
            ws.set_row(row, height)
            for first, last, value, key in cells:
                ws.merge_range(row, first, row, last, value, f[key])

        def spacer(row: int, height: float) -> None:
            # Constant-memory mode drops rows without cells, and their height with them: give each spacer a blank cell.
            ws.set_row(row, height)
            ws.write_blank(row, 0, None, f["blank"])

        def share(n: int) -> str:
            return f"{n / total:.1%} of source rows" if total else "no source rows"

        def plural(n: int, word: str) -> str:
            return f"{n:,} {word}{'' if n == 1 else 's'}"

        spacer(0, 6)
        period = f"{_month_label(months[0])} – {_month_label(months[-1])}" if months else "no revenue in this run"
        line(1, 26, [(slots[0][0], slots[2][1], "Order Data Quality Report", "title"),
                     (slots[3][0], slots[3][1], f"Revenue period: {period}", "period")])
        source = (f"{plural(len(files), 'export file')}" + (f" ({skipped} skipped as duplicate content)" if skipped else "")
                  + f"  ·  rules: {r['rules']['source']}  ·  amounts in {revenue_info['currency']}"
                  + f"  ·  revenue counts {statuses} orders")
        line(2, 16, [(slots[0][0], slots[3][1], source, "subtitle")])
        spacer(3, 8)

        kpis = [
            ("Source Records", float(total), "kpi_int",
             f"rows in {plural(len(files), 'export file')}" + (f", {skipped} skipped" if skipped else "")),
            ("Accepted Orders", float(rows["accepted"]), "kpi_int", f"{share(rows['accepted'])} · ready to use"),
            (f"{statuses} Revenue", float(Decimal(revenue_info["amount"])), "kpi_money",
             f"{plural(revenue_info['orders'], statuses.lower() + ' order')} · gross {revenue_info['currency']}"),
            ("Reconciliation Status", _status_label(r["status"]), "kpi_pass" if ok else "kpi_fail",
             f"{passed} of {len(checks)} checks passed · " + ("every row accounted for" if ok else
                                                              "see Reconciliation Audit")),
        ]
        accents = ["accent_navy", "accent_navy", "accent_navy", "accent_teal" if ok else "accent_red"]
        line(KPI_ROW, 3, [(a, b, "", accent) for (a, b), accent in zip(slots, accents)])
        line(KPI_ROW + 1, 17, [(a, b, label, "kpi_label") for (a, b), (label, *_) in zip(slots, kpis)])
        line(KPI_ROW + 2, 31, [(a, b, value, key) for (a, b), (_, value, key, _) in zip(slots, kpis)])
        line(KPI_ROW + 3, 16, [(a, b, note, "kpi_note") for (a, b), (*_, note) in zip(slots, kpis)])
        line(KPI_ROW + 4, 5, [(a, b, "", "card") for a, b in slots])
        spacer(KPI_ROW + 5, 10)

        conflicting, overridden = r["duplicates"]["conflicting_order_ids"], rows["conflict_overridden"]
        quarantine_note = (f"{plural(conflicting, 'order ID')} with conflicting versions" if not overridden else
                           f"{plural(overridden, 'version')} overridden by keep_first")
        indicators = [
            ("Rejected Rows", rows["rejected"], f"{share(rows['rejected'])} · see Rejected Records", "accent_amber"),
            ("Duplicates Ignored", rows["duplicate_ignored"], "exact copies, each counted once", "accent_slate"),
            ("Quarantined Rows", rows["quarantined_conflict"], quarantine_note, "accent_amber"),
        ]
        groups = []
        for text, names in CHECK_GROUPS:
            group = [c for c in checks if c["check"] in names]
            good = all(c["status"] == "PASS" for c in group)
            groups.append((f"{'✔' if good else '✖'}  {text}", "check_pass" if good else "check_fail"))
        check_slot = slots[3]
        line(CHECKS_ROW, 15, [(slots[0][0], slots[2][1], "DATA QUALITY", "section"),
                              (*check_slot, "RECONCILIATION CHECKS", "section")])
        line(CHECKS_ROW + 1, 3, [*((a, b, "", accent) for (a, b), (*_, accent) in zip(slots, indicators)),
                                 (*check_slot, "", "accent_teal" if ok else "accent_red")])
        for offset, height in ((2, 15), (3, 21), (4, 15)):
            cells = []
            for (a, b), (label, value, note, _) in zip(slots, indicators):
                cells.append((a, b, *{2: (label, "small_label"), 3: (float(value), "small_int"),
                                      4: (note, "small_note")}[offset]))
            text, key = groups[offset - 2]
            line(CHECKS_ROW + offset, height, [*cells, (*check_slot, text, key)])
        line(CHECKS_ROW + 5, 5, [(a, b, "", "card") for a, b in slots])
        spacer(CHECKS_ROW + 6, 10)

        height = CHART_ROWS * 20
        width = {i: _width_px(slots, i, i) for i in range(SLOTS)}
        if months:
            chart = self.wb.add_chart({"type": "column"})
            chart.add_series({"name": f"{statuses} revenue", "categories": revenue["months"],
                              "values": revenue["month_totals"], "fill": {"color": REVENUE},
                              "border": {"none": True}, "gap": 55,
                              "data_labels": _data_labels(f["chart_money"], "outside_end")})
            _style_chart(chart, f"Monthly {statuses} Revenue", vertical_bars=True)
            chart.set_size({"width": _width_px(slots, 0, 1), "height": height})
            ws.insert_chart(CHART_ROW, slots[0][0], chart, {"description": (
                f"Column chart: {statuses} revenue per month, from the totals on the {MONTHLY} sheet.")})
            chart = self.wb.add_chart({"type": "bar"})
            chart.add_series({"name": "Revenue by channel", "categories": revenue["channels"],
                              "values": revenue["channel_totals"], "fill": {"color": CHANNELS},
                              "border": {"none": True}, "gap": 70,
                              "data_labels": _data_labels(f["chart_money"], "inside_end", color="#FFFFFF")})
            _style_chart(chart, "Revenue by Sales Channel", vertical_bars=False)
            chart.set_size({"width": width[2], "height": height})
            ws.insert_chart(CHART_ROW, slots[2][0], chart, {"description": (
                f"Bar chart: {statuses} revenue per sales channel, from the totals on the {MONTHLY} sheet.")})
        if issues:
            chart = self.wb.add_chart({"type": "bar"})
            chart.add_series({"name": "Rejected rows", "categories": issues["labels"], "values": issues["counts"],
                              "fill": {"color": ISSUES}, "border": {"none": True}, "gap": 45,
                              "data_labels": _data_labels(f["chart_int"], "outside_end")})
            _style_chart(chart, "Data Quality Issues", vertical_bars=False)
            chart.set_size({"width": width[3], "height": height})
            ws.insert_chart(CHART_ROW, slots[3][0], chart, {"description": (
                f"Bar chart: rejected rows per reason, from the {QUALITY} sheet.")})
        notes = []
        if not months:
            notes.append((slots[0][0], slots[2][1], f"No accepted {statuses} orders in this run: no revenue charts.",
                          "note"))
        if not issues:
            notes.append((*slots[3], "No rejected rows in this run.", "note"))
        if notes:
            line(CHART_ROW, 15, notes)
        spacer(FOOTER_ROW - 1, 6)
        line(FOOTER_ROW, 14, [(slots[0][0], slots[3][1],
                               "All figures come from the reconciled outputs (clean_orders.csv, monthly_revenue.csv, "
                               "quality_report.json). Row accounting and every check: Reconciliation Audit sheet.",
                               "footer")])

    # Monthly revenue ---------------------------------------------------------------------------------------

    def _monthly(self, monthly: list[dict[str, str]]) -> tuple[list[str], dict[str, str]]:
        ws, f = self.ws[MONTHLY], self.f
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
        ws.set_row(0, 28)
        ws.write_string(0, 0, f"{statuses} Revenue by Month and Sales Channel", f["title"])
        ws.write_string(1, 0, f"Accepted orders with status {statuses}. Gross amounts in {revenue['currency']}, "
                              "before taxes and fees. Same data as monthly_revenue.csv.", f["subtitle"])
        cells: dict[str, str] = {}
        if not months:
            ws.write_string(3, 0, f"No accepted {statuses} orders in this run.", f["note"])
            return [], cells
        row = 3
        blocks = (("Revenue", amount, f["money"], f["total_money"], Decimal("0.00")),
                  (f"{statuses} orders", orders, f["int"], f["total_int"], 0))
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
            if block == 0:
                if len(channels) <= len(SERIES):  # more channels than colors: the table alone shows them
                    self._channel_month_chart(ws, channels, first, row - 1, last)
                sheet = f"'{MONTHLY}'"
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
        return months, cells

    def _channel_month_chart(self, ws: Worksheet, channels: list[str], first: int, last_row: int, last: int) -> None:
        chart = self.wb.add_chart({"type": "column"})
        for col, channel in enumerate(channels, start=1):
            chart.add_series({"name": [MONTHLY, first - 1, col], "values": [MONTHLY, first, col, last_row, col],
                              "categories": [MONTHLY, first, 0, last_row, 0],
                              "fill": {"color": SERIES[col - 1]}, "border": {"none": True}, "gap": 80, "overlap": -8})
        statuses = ", ".join(self.report["revenue"]["statuses"])
        _style_chart(chart, f"{statuses} Revenue by Month and Channel", vertical_bars=True,
                     value_format=self.f["axis_money"])
        chart.set_legend({"position": "bottom", "font": {"name": "Calibri", "size": 9, "color": INK_2}})
        chart.set_size({"width": 620, "height": 320})
        ws.insert_chart(3, last + 2, chart, {"description": f"Column chart: {statuses} revenue per month and channel, "
                                                           "from the revenue table on this sheet."})

    # Data quality summary ----------------------------------------------------------------------------------

    def _data_quality(self) -> dict[str, str]:
        ws, f, r = self.ws[QUALITY], self.f, self.report
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 24)
        ws.set_column(1, 1, 22)
        ws.set_column(2, 2, 76)
        ws.set_column(3, 4, 16)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.set_row(0, 28)
        ws.write_string(0, 0, "Data Quality Summary", f["title"])
        ws.write_string(1, 0, "Why rows were rejected, how duplicates were handled, what was standardized "
                              "automatically and which corrections wait for approval.", f["subtitle"])
        row = 3
        rejected = r["rows"]["rejected"]
        issues = sorted(r["issues"].items(), key=lambda item: (-item[1], item[0]))
        ws.write_string(row, 0, "Rejected rows by reason", f["h2"])
        row += 1
        for col, text in enumerate(("Issue", "Reason code", "What it means", "Rows", "Share of rejected")):
            ws.write_string(row, col, text, f["header_num" if col >= 3 else "header"])
        row += 1
        first = row
        for code, count in issues:
            ws.write_string(row, 0, _issue_label(code), f["cell"])
            ws.write_string(row, 1, code, f["code"])
            ws.write_string(row, 2, _issue_text(code), f["cell"])
            ws.write_number(row, 3, count, f["int"])
            ws.write_number(row, 4, count / rejected if rejected else 0, f["pct"])
            row += 1
        cells: dict[str, str] = {}
        if issues:
            ws.conditional_format(first, 3, row - 1, 3, {"type": "data_bar", "bar_color": DATA_BAR, "bar_solid": True,
                                                       "min_type": "num", "min_value": 0})
            sheet = f"'{QUALITY}'"
            cells = {"labels": f"={sheet}!$A${first + 1}:$A${row}", "counts": f"={sheet}!$D${first + 1}:$D${row}"}
            ws.write_string(row, 0, "A row can fail more than one rule, so the shares can add up to more than 100%.",
                            f["note"])
        else:
            ws.write_string(row, 0, "No rows were rejected.", f["note"])
        row += 2

        def table(title: str, headers: tuple[str, str, str], lines: list[tuple[str, str, Any, str]]) -> None:
            nonlocal row
            ws.write_string(row, 0, title, f["h2"])
            row += 1
            ws.write_string(row, 0, headers[0], f["header"])
            ws.merge_range(row, 1, row, 2, headers[1], f["header"])
            ws.write_string(row, 3, headers[2], f["header_num"])
            row += 1
            for label, meaning, value, label_key in lines:
                ws.write_string(row, 0, label, f[label_key])
                ws.merge_range(row, 1, row, 2, meaning, f["cell"])
                if isinstance(value, str):
                    ws.write_string(row, 3, value, f["code_right"])
                else:
                    ws.write_number(row, 3, value, f["int"])
                row += 1

        d = r["duplicates"]
        table("Duplicates", ("Measure", "What it means", "Count"), [
            ("Exact duplicate order IDs", "Order IDs that arrived more than once with identical values",
             d["exact_duplicate_order_ids"], "cell"),
            ("Duplicate rows ignored", "The extra copies; each order is counted once", d["duplicate_rows_ignored"], "cell"),
            ("Conflicting order IDs", "Order IDs that arrived in two or more different versions",
             d["conflicting_order_ids"], "cell"),
            ("Rows quarantined", "Every version of a conflicting order ID, held back for review", d["quarantined_rows"],
             "cell"),
            ("Conflicts overridden", "Versions replaced by the first one (only with the keep_first policy)",
             d["overridden_rows"], "cell"),
            ("Conflict policy", "quarantine_all: no version of a conflicting order is used until it is resolved"
             if d["policy"] == "quarantine_all" else
             "keep_first: the first version (by file name and line) is used; the others are listed", d["policy"], "cell"),
        ])
        row += 1

        normalizations = [(field, NORMALIZATION_TEXT.get(kind, kind), count, "code")
                          for field, kinds in sorted(r["normalizations"].items()) for kind, count in sorted(kinds.items())]
        table("Standardized automatically (no approval needed)", ("Field", "Change", "Values changed"), normalizations)
        ws.write_string(row, 0, "No values needed standardizing." if not normalizations else
                        "Counted over every source row. Financial values, customer and order IDs are never changed "
                        "automatically.", f["note"])
        row += 2

        c = r["corrections"]
        table("Suggested corrections (applied only after approval)", ("Status", "What it means", "Suggestions"), [
            ("Suggested", "Rejected values with one safe, unambiguous fix", c["suggested"], "cell"),
            ("Applied", "Approved by a reviewer and applied on this run", c["applied"], "cell"),
            ("Rejected by reviewer", "The reviewer decided against the fix; the row stays rejected", c["rejected"], "cell"),
            ("Pending review", "Waiting for a decision; the row stays rejected", c["pending"], "cell"),
            ("Stale decisions", "Decisions whose row no longer matches the input; not applied", c["stale"], "cell"),
            *((f"Rule: {rule}", RULE_TEXT.get(rule, rule), count, "code") for rule, count in c["by_rule"].items()),
        ])
        ws.write_string(row, 0, "Details per suggestion are on the Correction Log sheet.", f["note"])
        return cells

    # Reconciliation audit ----------------------------------------------------------------------------------

    def _audit(self) -> None:
        """The technical evidence behind the dashboard: row accounting, every check, the inputs and the rules."""
        ws, f, r = self.ws[AUDIT], self.f, self.report
        rows, checks = r["rows"], r["checks"]
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 42)
        ws.set_column(1, 1, 24)
        ws.set_column(2, 3, 13)
        ws.set_column(4, 4, 68)
        ws.set_column(5, 5, 24)
        ws.set_landscape()
        ws.fit_to_pages(1, 0)
        ws.set_row(0, 28)
        ws.write_string(0, 0, "Reconciliation Audit", f["title"])
        ws.write_string(1, 0, "The evidence behind the dashboard: where every source row went, each reconciliation "
                              "check, the input files and the rules. Per-run file actions are in processing_audit.csv "
                              "and run_log.jsonl.", f["subtitle"])
        passed = sum(c["status"] == "PASS" for c in checks)
        ws.set_row(3, 26)
        if r["status"] == "PASS":
            status = f"✔ PASS   All {len(checks)} reconciliation checks passed."
        else:
            status = (f"✖ FAIL   {len(checks) - passed} of {len(checks)} reconciliation checks failed. Do not use the "
                      "totals until the failed checks are resolved.")
        ws.merge_range(3, 0, 3, 5, status, f["status_pass" if r["status"] == "PASS" else "status_fail"])
        row = 5

        total = rows["input"]
        ws.write_string(row, 0, "Row accounting: where every source row went", f["h2"])
        row += 1
        ws.write_string(row, 0, "Outcome", f["header"])
        ws.write_string(row, 1, "Rows", f["header_num"])
        ws.write_string(row, 2, "Share", f["header_num"])
        ws.merge_range(row, 3, row, 5, "What it means", f["header"])
        row += 1
        for outcome in codes.OUTCOMES:
            count = rows[outcome.lower()]
            if outcome == codes.CONFLICT_OVERRIDDEN and not count:
                continue
            label, meaning = OUTCOME_TEXT[outcome]
            ws.write_string(row, 0, label, f["cell"])
            ws.write_number(row, 1, count, f["int"])
            ws.write_number(row, 2, count / total if total else 0, f["pct"])
            ws.merge_range(row, 3, row, 5, meaning, f["cell"])
            row += 1
        ws.write_string(row, 0, "Source rows", f["total"])
        ws.write_number(row, 1, total, f["total_int"])
        ws.write_number(row, 2, 1 if total else 0, f["total_pct"])
        ws.merge_range(row, 3, row, 5, "the sum of the outcomes above", f["total"])
        row += 3

        ws.write_string(row, 0, "Reconciliation checks", f["h2"])
        row += 1
        ws.write_string(row, 0, "Check", f["header"])
        ws.write_string(row, 1, "Status", f["header"])
        ws.merge_range(row, 2, row, 5, "Detail", f["header"])
        row += 1
        for check in checks:
            ws.set_row(row, 30)
            ws.write_string(row, 0, CHECK_TEXT.get(check["check"], check["check"]), f["cell_wrap"])
            ws.write_string(row, 1, _status_label(check["status"]), f["pass" if check["status"] == "PASS" else "fail"])
            ws.merge_range(row, 2, row, 5, check["detail"], f["cell_wrap"])
            row += 1
        ws.write_string(row, 0, "The checks re-read clean_orders.csv and monthly_revenue.csv as written; they do not "
                                "reuse the counters that produced them.", f["note"])
        row += 3

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
            ws.write_string(row, 5, item["duplicate_of"] or "", f["cell"])
            row += 1
        ws.write_string(row, 0, "Source rows processed", f["total"])
        ws.write_string(row, 1, "", f["total"])
        ws.write_number(row, 2, total, f["total_int"])
        row += 3

        rules, reference, revenue = r["rules"], r["reference"], r["revenue"]
        ws.write_string(row, 0, "Rules and reference data", f["h2"])
        row += 1
        ws.write_string(row, 0, "Setting", f["header"])
        ws.merge_range(row, 1, row, 5, "Value", f["header"])
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
            ws.merge_range(row, 1, row, 5, value, f["mono"] if "SHA-256" in label else f["cell"])
            row += 1

    # Detail sheets -----------------------------------------------------------------------------------------

    def _rejected(self) -> None:
        columns = [("Source file", 20, "text"), ("Line", 8, "int"), ("Order ID", 14, "text"), ("Reasons", 30, "text"),
                   *((f"Received: {name}", width, "text") for name, width in (
                       ("order ID", 16), ("customer ID", 14), ("order date", 14), ("SKU", 13), ("quantity", 11),
                       ("amount", 12), ("channel", 14), ("status", 13))),
                   ("Extra fields", 16, "text")]
        self._detail("Rejected Records", "Rejected Records", outputs.REJECTED, columns, "rejected rows",
                     "Values are shown exactly as received. Text that Excel could run as a formula starts with an "
                     "apostrophe.", freeze_cols=3)

    def _duplicates(self) -> None:
        columns = [("Order ID", 14, "text"), ("Classification", 24, "text"), ("Outcome", 24, "text"),
                   ("Occurrences", 12, "int"), ("Versions", 10, "int"), ("Version", 9, "int"),
                   ("Differing fields", 18, "text"), ("Source file", 20, "text"), ("Line", 8, "int"),
                   ("Customer ID", 13, "text"), ("Order date", 12, "date"), ("SKU", 11, "text"),
                   ("Quantity", 10, "int"), ("Amount", 12, "money"), ("Channel", 13, "text"), ("Status", 12, "text")]
        ws = self._detail("Duplicate Review", "Duplicate Review", outputs.DUPLICATES, columns,
                          "rows of repeated order IDs",
                          "One row per occurrence. Conflicting versions are highlighted; the differing values are "
                          "marked.", freeze_cols=1)
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

        ws = self._detail("Correction Log", "Correction Log", outputs.CORRECTIONS_APPLIED, columns, "suggestions",
                          "Suggestions are applied only after a reviewer writes APPROVE in correction_review.csv and "
                          "the pipeline runs again with --corrections. Rejected and pending suggestions change nothing.",
                          freeze_cols=1, rows=rows(), empty="No correction suggestions in this run: no rejected value "
                          "had a single safe fix.")
        if ws is None:
            return
        last = FIRST_DATA_ROW + self.shown - 1
        for status in REVIEW_STATUS_FILL:
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

        self._detail("Clean Orders", "Clean Orders", outputs.CLEAN, columns, "accepted orders, sorted by order ID",
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
        ws.set_row(0, 28)
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
        ws.set_row(3, 20)
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


# Helpers -----------------------------------------------------------------------------------------------------

def _formats(wb: xlsxwriter.Workbook, currency: str) -> dict[str, Any]:
    money = "$#,##0.00" if currency == "USD" else f'#,##0.00 "{currency}"'
    whole_money = "$#,##0" if currency == "USD" else f'#,##0 "{currency}"'
    base = {"font_name": "Calibri", "font_size": 10, "font_color": INK, "valign": "vcenter"}

    def fmt(**props: Any):
        return wb.add_format({**base, **props})

    edge = {"bottom": 1, "bottom_color": RULE}
    card = {"bg_color": SURFACE}
    total = {"bold": True, "top": 1, "top_color": NAVY, "bottom": 1, "bottom_color": NAVY}
    return {
        "title": fmt(bold=True, font_size=18, font_color=NAVY),
        "subtitle": fmt(font_color=INK_2),
        "period": fmt(font_color=INK_2, align="right"),
        "h2": fmt(bold=True, font_size=12, font_color=NAVY),
        "section": fmt(bold=True, font_size=9, font_color=MUTED),
        "footer": fmt(italic=True, font_size=8, font_color=MUTED),
        "header": fmt(bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True, border=1, border_color=NAVY),
        "header_num": fmt(bold=True, font_color="#FFFFFF", bg_color=NAVY, text_wrap=True, align="right", border=1,
                          border_color=NAVY),
        "cell": fmt(**edge),
        "cell_wrap": fmt(text_wrap=True, **edge),
        "code": fmt(font_name="Consolas", **edge),
        "code_right": fmt(font_name="Consolas", align="right", **edge),
        "mono": fmt(font_name="Consolas", font_size=9, **edge),
        "int": fmt(num_format="#,##0", **edge),
        "money": fmt(num_format=money, **edge),
        "pct": fmt(num_format="0.0%", **edge),
        "date": fmt(num_format="yyyy-mm-dd", align="left", **edge),
        "total": fmt(**total),
        "total_int": fmt(num_format="#,##0", **total),
        "total_money": fmt(num_format=money, **total),
        "total_pct": fmt(num_format="0.0%", **total),
        "note": fmt(italic=True, font_color=INK_2),
        "banner": fmt(bold=True, font_color=WARN_INK, bg_color=WARN_FILL),
        "pass": fmt(bold=True, font_color=GOOD_INK, bg_color=GOOD_FILL, align="center", **edge),
        "fail": fmt(bold=True, font_color=BAD_INK, bg_color=BAD_FILL, align="center", **edge),
        "status_pass": fmt(bold=True, font_size=12, font_color=GOOD_INK, bg_color=GOOD_FILL, indent=1),
        "status_fail": fmt(bold=True, font_size=12, font_color=BAD_INK, bg_color=BAD_FILL, indent=1),
        # Dashboard cards: a colored accent row, then label, value and note rows on a light surface.
        "accent_navy": fmt(bg_color=NAVY),
        "accent_teal": fmt(bg_color=TEAL),
        "accent_amber": fmt(bg_color=AMBER),
        "accent_slate": fmt(bg_color=SLATE),
        "accent_red": fmt(bg_color=BAD_INK),
        "card": fmt(**card),
        "blank": wb.add_format(),
        "kpi_label": fmt(bold=True, font_color=INK_2, indent=1, **card),
        "kpi_int": fmt(bold=True, font_size=24, font_color=NAVY, num_format="#,##0", align="left", indent=1, **card),
        "kpi_money": fmt(bold=True, font_size=24, font_color=NAVY, num_format=money, align="left", indent=1, **card),
        "kpi_pass": fmt(bold=True, font_size=24, font_color=TEAL, indent=1, **card),
        "kpi_fail": fmt(bold=True, font_size=24, font_color=BAD_INK, indent=1, **card),
        "kpi_note": fmt(font_size=9, font_color=INK_2, indent=1, **card),
        "small_label": fmt(bold=True, font_size=9, font_color=INK_2, indent=1, **card),
        "small_int": fmt(bold=True, font_size=16, font_color=NAVY, num_format="#,##0", align="left", indent=1, **card),
        "small_note": fmt(font_size=9, font_color=INK_2, indent=1, **card),
        "check_pass": fmt(font_color=GOOD_INK, indent=1, **card),
        "check_fail": fmt(bold=True, font_color=BAD_INK, indent=1, **card),
        "band": wb.add_format({"bg_color": BAND}),
        "cf_good": wb.add_format({"bg_color": GOOD_FILL, "font_color": GOOD_INK}),
        "cf_neutral": wb.add_format({"bg_color": NEUTRAL_FILL, "font_color": INK_2}),
        "cf_warn": wb.add_format({"bg_color": WARN_FILL, "font_color": WARN_INK, "bold": True}),
        "cf_diff": wb.add_format({"bg_color": "#F6DDB4", "font_color": "#6B3F00", "bold": True}),
        "cf_applied": wb.add_format({"bg_color": GOOD_FILL, "font_color": GOOD_INK, "bold": True}),
        "cf_rejected": wb.add_format({"bg_color": BAD_FILL, "font_color": BAD_INK, "bold": True}),
        "cf_pending": wb.add_format({"bg_color": WARN_FILL, "font_color": WARN_INK, "bold": True}),
        "cf_stale": wb.add_format({"bg_color": NEUTRAL_FILL, "font_color": INK_2}),
        "axis_money": f'{whole_money.replace("#,##0", "#,##0,")}"K"' if currency == "USD" else whole_money,
        "chart_money": whole_money,
        "chart_int": "#,##0",
    }


def _slot(index: int) -> tuple[int, int]:
    """First and last column of dashboard card slot `index` (column A is the left margin)."""
    first = 1 + index * (SLOT_COLS + 1)
    return first, first + SLOT_COLS - 1


def _col_px(width: float) -> int:
    """Pixel width of a column, as Excel computes it for the default font."""
    return int(width * 7 + 0.5) + 5


def _width_px(slots: list[tuple[int, int]], first: int, last: int) -> int:
    """Pixel width from the start of slot `first` to the end of slot `last`, including the gaps between them."""
    columns = slots[last][1] - slots[first][0] + 1
    gaps = last - first
    return (columns - gaps) * _col_px(SLOT_WIDTH) + gaps * _col_px(GAP_WIDTH)


def _data_labels(num_format: str, position: str, color: str = INK) -> dict[str, Any]:
    return {"value": True, "num_format": num_format, "position": position,
            "font": {"name": "Calibri", "size": 9, "color": color, "bold": color != INK}}


def _style_chart(chart, title: str, vertical_bars: bool, value_format: str | None = None) -> None:
    """Title, no legend, light category axis. Without `value_format` the value axis is hidden: data labels carry it."""
    font = {"name": "Calibri", "color": INK_2, "size": 9}
    chart.set_title({"name": title, "name_font": {"name": "Calibri", "size": 11, "bold": True, "color": NAVY},
                     "overlay": False})
    chart.set_legend({"none": True})
    if value_format:
        values = {"num_format": value_format, "num_font": font, "line": {"none": True}, "min": 0,
                  "major_gridlines": {"visible": True, "line": {"color": RULE, "width": 0.75}}}
    else:
        values = {"visible": False, "min": 0, "major_gridlines": {"visible": False}}
    categories = {"num_font": font, "line": {"color": AXIS}, "major_tick_mark": "none"}
    if vertical_bars:
        chart.set_x_axis(categories)
        chart.set_y_axis(values)
    else:  # bar chart: categories on the vertical axis, first category at the top
        chart.set_y_axis({**categories, "reverse": True})
        chart.set_x_axis(values)
    chart.set_chartarea({"border": {"none": True}, "fill": {"none": True}})
    chart.set_plotarea({"border": {"none": True}, "fill": {"none": True}})


def _status_label(status: str) -> str:
    return "✔ PASS" if status == "PASS" else "✖ FAIL"


def _issue_text(code: str) -> str:
    if code in codes.ISSUE_CODES:
        return codes.ISSUE_CODES[code]
    if code.startswith(codes.MISSING_PREFIX):
        return f"The required field {code[len(codes.MISSING_PREFIX):].lower()} is empty."
    return code


def _issue_label(code: str) -> str:
    """A short plain-language name for a reason code, for chart categories."""
    if code in ISSUE_LABEL:
        return ISSUE_LABEL[code]
    if code.startswith(codes.MISSING_PREFIX):
        words = code[len(codes.MISSING_PREFIX):].lower().split("_")
        return "Missing " + " ".join(w.upper() if w in ("id", "sku", "usd") else w for w in words)
    return code.replace("_", " ").capitalize()


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
