"""Draw assets/before-after.svg: example rows as they arrived, and what the pipeline made of them.

Usage, from the repository root with the virtual environment active:

    python scripts/make-before-after.py [--out assets/before-after.svg]

Runs the pipeline on the three original exports plus the late batch, with the demo correction decisions, into a
temporary folder. Then picks examples from its outputs by fixed rules: the first accepted row that needed the most
standardization, the first rows rejected only for an amount mismatch and only for an invalid quantity, the first exact
duplicate that spans two files, the first two-version conflict that involves the late batch, and the first applied and
the first pending correction. Every value in the picture is read from the input files or the run's outputs.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from order_quality import errors as codes
from order_quality.config import COLUMNS
from order_quality.reader import read_records

ROOT = Path(__file__).resolve().parents[1]
INPUTS = (ROOT / "data" / "raw", ROOT / "data" / "late-batch")
DECISIONS = ROOT / "corrections" / "demo-decisions.csv"

FONT, MONO = "Segoe UI, Helvetica, Arial, sans-serif", "Consolas, Menlo, monospace"
INK, MUTED, RULE, HEAD = "#1F2937", "#6B7280", "#E5E7EB", "#1F4E8C"
AFTER_BG = "#F5F7FA"
STANDARDIZED, ISSUE, CONFLICT, SUGGESTION, CLEANED = "#FFF2B3", "#F9D2D0", "#FAD9BF", "#D6E4FA", "#D5EFD8"
BADGES = {
    codes.ACCEPTED: ("Accepted", "#E3F4E3", "#006300"),
    codes.REJECTED: ("Rejected", "#FBE3E3", "#B42318"),
    codes.DUPLICATE_IGNORED: ("Duplicate ignored", "#EDEDEA", "#475467"),
    codes.QUARANTINED_CONFLICT: ("Quarantined", "#FDE9D9", "#B54708"),
}
TABLE = (("source", "Source", 124), ("order_id", "Order ID", 100), ("customer_id", "Customer", 82),
         ("order_date", "Date", 104), ("sku", "SKU", 88), ("quantity", "Qty", 44), ("amount_usd", "Amount", 80),
         ("channel", "Channel", 112), ("status", "Status", 104), ("outcome", "Outcome", 190))
ISSUE_FIELD = {codes.INVALID_DATE: "order_date", codes.UNKNOWN_SKU: "sku", codes.INVALID_QUANTITY: "quantity",
               codes.INVALID_AMOUNT: "amount_usd", codes.AMOUNT_MISMATCH: "amount_usd",
               codes.UNKNOWN_CHANNEL: "channel", codes.UNKNOWN_STATUS: "status"}
FIELD_NAME = {"order_id": "order ID", "customer_id": "customer", "order_date": "date", "sku": "SKU",
              "quantity": "quantity", "amount_usd": "amount", "channel": "channel", "status": "status"}
MARGIN, ROW_H, SECTION_H = 24, 28, 34
WIDTH = 2 * MARGIN + sum(w for _, _, w in TABLE)


# Data ----------------------------------------------------------------------------------------------------------

def run_pipeline(out: Path) -> None:
    args = [sys.executable, "-m", "order_quality", "run", *[a for p in INPUTS for a in ("--input", str(p))],
            "--reference", str(ROOT / "data" / "reference"), "--corrections", str(DECISIONS), "--out", str(out),
            "--no-workbook", "--quiet"]
    subprocess.run(args, check=True, cwd=ROOT, stdout=subprocess.DEVNULL)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def key(row: dict[str, str]) -> tuple[str, int]:
    return row["source_file"], int(row["source_line"])


def first(rows, condition, what: str):
    found = next((r for r in rows if condition(r)), None)
    if found is None:
        raise SystemExit(f"no example found for: {what}")
    return found


def examples(out: Path) -> list[tuple[str, str, list[list[dict]]]]:
    files = {p.name: p for folder in INPUTS for p in sorted(folder.glob("*.csv"))}
    late = sorted(p.name for p in INPUTS[1].glob("*.csv"))[0]
    raw = {(name, r.line): r.values for name, path in files.items() for r in read_records(path, "utf-8-sig")}
    clean = {key(r): r for r in read_csv(out / "clean_orders.csv")}
    outcomes = read_csv(out / "row_outcomes.csv")
    corrections = sorted(read_csv(out / "corrections_applied.csv"), key=key)
    groups: dict[str, list[dict]] = {}
    for row in read_csv(out / "duplicates_review.csv"):
        groups.setdefault(row["order_id"], []).append(row)

    def changed(k) -> list[str]:
        return [c for c in COLUMNS if raw[k][c] != clean[k][c]]

    def before(k, marks: dict[str, str]) -> dict:
        return {"source": k, "values": raw[k], "marks": marks}

    def accepted(k, field: str | None = None, note: str = "") -> list[dict]:
        marks = {c: STANDARDIZED for c in changed(k)}
        if field:
            marks[field] = SUGGESTION
        return [before(k, marks), {"after": True, "values": clean[k], "marks": {c: CLEANED for c in changed(k)},
                                   "outcome": codes.ACCEPTED, "note": note}]

    def rejected(k, message: str, field: str | None = None) -> list[dict]:
        codes_ = next(r for r in outcomes if key(r) == k)["issue_codes"].split("|")
        marks = {ISSUE_FIELD.get(c, c.removeprefix(codes.MISSING_PREFIX).lower()): ISSUE for c in codes_}
        if field:
            marks[field] = SUGGESTION
        return [before(k, marks), {"after": True, "message": message, "outcome": codes.REJECTED}]

    tidy = [key(r) for r in outcomes if r["outcome"] == codes.ACCEPTED and not r["corrected_fields"]]
    tidy_key = max(tidy, key=lambda k: len(changed(k)))  # max() keeps the first of equals: earliest file and line

    mismatch = key(first(outcomes, lambda r: r["issue_codes"] == codes.AMOUNT_MISMATCH, "amount mismatch"))
    quantity = key(first(outcomes, lambda r: r["issue_codes"] == codes.INVALID_QUANTITY, "invalid quantity"))

    exact = sorted(first(groups.values(), lambda g: g[0]["classification"] == codes.EXACT_DUPLICATE and len(g) == 2
                         and len({r["source_file"] for r in g}) == 2, "exact duplicate across files"), key=key)
    conflict = sorted(first(groups.values(), lambda g: g[0]["classification"] == codes.CONFLICTING_DUPLICATE
                            and len(g) == 2 and late in {r["source_file"] for r in g}, "late conflict"), key=key)
    differing = conflict[0]["differing_fields"].split("|")

    approved = first(corrections, lambda r: r["status"] == "APPLIED", "applied correction")
    pending = first(corrections, lambda r: r["status"] == "PENDING", "pending correction")
    pending_code = next(r for r in outcomes if key(r) == key(pending))["issue_codes"]

    return [
        ("Standardized automatically", "extra spaces, letter case and agreed date formats; no approval needed",
         [accepted(tidy_key)]),
        ("Rejected with a reason", "kept out of every total, listed with its file, line and reason",
         [rejected(mismatch, f"{codes.AMOUNT_MISMATCH}: {codes.ISSUE_CODES[codes.AMOUNT_MISMATCH]}"),
          rejected(quantity, f"{codes.INVALID_QUANTITY}: {codes.ISSUE_CODES[codes.INVALID_QUANTITY]}")]),
        ("Duplicates", "compared after standardization, over all files, whatever order they arrive in",
         [[before(key(r), {}) for r in exact]
          + [{"after": True, "outcome": codes.DUPLICATE_IGNORED,
              "message": "Identical orders: the first row is accepted, the copy is counted once."}],
          [before(key(r), {f: CONFLICT for f in differing}) for r in conflict]
          + [{"after": True, "outcome": codes.QUARANTINED_CONFLICT,
              "message": f"Same order ID, different {' and '.join(FIELD_NAME[f] for f in differing)}: "
                         "both versions are held back until someone decides."}]]),
        ("Corrections that need approval", "a suggested fix is applied only after a reviewer approves it",
         [accepted(key(approved), approved["field"], "fix approved"),
          rejected(key(pending), f"{pending_code}: the suggested “{pending['suggested_value']}” waits for a "
                                 "reviewer; nothing changes until it is approved.", pending["field"])]),
    ]


# Drawing -------------------------------------------------------------------------------------------------------

def text(x: float, y: float, value: str, size: int = 13, fill: str = INK, weight: int = 400, family: str = FONT,
         italic: bool = False) -> str:
    style = ' font-style="italic"' if italic else ""
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}"{style}>{escape(value)}</text>')


def rect(x: float, y: float, w: float, h: float, fill: str, rx: int = 0) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}"/>'


def shown(value: str) -> str:
    """Make spaces at the start or end visible as middle dots."""
    lead, trail = len(value) - len(value.lstrip(" ")), len(value) - len(value.rstrip(" "))
    return "·" * lead + value.strip(" ") + "·" * trail if value.strip(" ") else "·" * len(value)


def source_label(k: tuple[str, int]) -> str:
    match = re.fullmatch(r"orders_export_(\d+)\.csv", k[0])
    return f"{'export ' + match.group(1) if match else k[0]}, line {k[1]:,}"


def draw_row(parts: list[str], y: int, row: dict) -> None:
    columns, x = {}, MARGIN
    for name, _, width in TABLE:
        columns[name] = (x, width)
        x += width
    if row.get("after"):
        parts.append(rect(MARGIN, y, WIDTH - 2 * MARGIN, ROW_H, AFTER_BG))
        parts.append(text(columns["source"][0] + 8, y + 19, "↳ result", 12, MUTED, italic=True))
    else:
        parts.append(text(columns["source"][0] + 8, y + 19, source_label(row["source"]), 12, MUTED))
    if "values" in row:
        for name in COLUMNS:
            cx, width = columns[name]
            if name in row.get("marks", {}):
                parts.append(rect(cx + 2, y + 3, width - 6, ROW_H - 6, row["marks"][name], rx=4))
            value = row["values"][name]
            parts.append(text(cx + 7, y + 19, shown(value), 13, INK, family=MONO) if value
                         else text(cx + 7, y + 19, "(empty)", 12, MUTED, italic=True))
    if "message" in row:
        parts.append(text(columns["order_id"][0] + 7, y + 19, row["message"], 13, INK))
    if "outcome" in row:
        label, fill, ink = BADGES[row["outcome"]]
        bx = columns["outcome"][0] + 6
        width = 7 * len(label) + 18
        parts.append(rect(bx, y + 4, width, ROW_H - 8, fill, rx=10))
        parts.append(text(bx + 9, y + 18, label, 12, ink, 700))
        if row.get("note"):
            parts.append(text(bx + width + 8, y + 18, row["note"], 12, MUTED))


def render(sections) -> str:
    parts = [text(MARGIN, 40, "What happens to each row", 22, INK, 700),
             text(MARGIN, 64, "Real rows from the demo data: as they arrived (white) and what the pipeline made of "
                              "them (grey).", 13, MUTED)]
    y = 82
    parts.append(rect(MARGIN, y, WIDTH - 2 * MARGIN, 30, HEAD))
    x = MARGIN
    for _, title, width in TABLE:
        parts.append(text(x + 7, y + 20, title, 13, "#FFFFFF", 700))
        x += width
    y += 30
    for title, description, groups in sections:
        parts.append(text(MARGIN + 2, y + 23, title, 14, HEAD, 700))
        parts.append(text(MARGIN + 2 + 8.2 * len(title) + 12, y + 23, description, 12, MUTED))
        y += SECTION_H
        for group in groups:
            for row in group:
                draw_row(parts, y, row)
                y += ROW_H
            parts.append(f'<path d="M{MARGIN},{y} H{WIDTH - MARGIN}" stroke="{RULE}" stroke-width="1"/>')
    y += 28
    x = MARGIN
    for fill, label in ((STANDARDIZED, "standardized automatically"), (ISSUE, "reason for rejection"),
                        (CONFLICT, "conflicting values"), (SUGGESTION, "suggested correction"),
                        (CLEANED, "value after cleaning")):
        parts.append(rect(x, y - 12, 16, 16, fill, rx=3))
        parts.append(text(x + 22, y + 1, label, 12, MUTED))
        x += 22 + 6.4 * len(label) + 22
    y += 26
    parts.append(text(MARGIN, y, "· marks a space at the start or end of a value. Generated by "
                                 "scripts/make-before-after.py from data/raw, data/late-batch and "
                                 "corrections/demo-decisions.csv.", 11, MUTED))
    height = y + 20
    body = "\n  ".join(parts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" '
            f'height="{height}">\n  <title>Example order rows before and after the data-quality pipeline</title>\n'
            f'  <rect width="{WIDTH}" height="{height}" fill="#FFFFFF"/>\n  {body}\n</svg>\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "assets" / "before-after.svg")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as work:
        run_pipeline(Path(work))
        svg = render(examples(Path(work)))
    args.out.write_text(svg, encoding="utf-8", newline="\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
