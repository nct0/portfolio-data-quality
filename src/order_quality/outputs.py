"""Business outputs, reconciliation checks and the quality report, all derived from the staged rows.

Every query has an explicit sort order, so the same staged rows always produce the same bytes.
"""

from __future__ import annotations

import csv
from collections import Counter
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import Any

from order_quality import corrections as cr
from order_quality import errors as codes
from order_quality.config import COLUMNS, Rules
from order_quality.export import CSV_ENCODING, write_csv
from order_quality.normalize import format_cents
from order_quality.reference import Reference
from order_quality.state import State

CLEAN = "clean_orders.csv"
REJECTED = "rejected_rows.csv"
DUPLICATES = "duplicates_review.csv"
MONTHLY = "monthly_revenue.csv"
ROW_OUTCOMES = "row_outcomes.csv"
CORRECTION_REVIEW = "correction_review.csv"
CORRECTIONS_APPLIED = "corrections_applied.csv"
QUALITY_REPORT = "quality_report.json"
PROCESSING_AUDIT = "processing_audit.csv"
RUN_LOG = "run_log.jsonl"
# Outputs that depend only on the inputs, rules, reference data and decisions (not on run history).
BUSINESS_OUTPUTS = (CLEAN, REJECTED, DUPLICATES, MONTHLY, ROW_OUTCOMES, CORRECTION_REVIEW, CORRECTIONS_APPLIED,
                    QUALITY_REPORT)

CLEAN_COLUMNS = (*COLUMNS, "source_file", "source_line")
REJECTED_COLUMNS = ("source_file", "source_line", "order_id", "issue_codes", *(f"raw_{c}" for c in COLUMNS),
                    "raw_extra_fields")
DUPLICATE_COLUMNS = ("order_id", "classification", "outcome", "occurrences", "versions", "version", "differing_fields",
                     "source_file", "source_line", "customer_id", "order_date", "sku", "quantity", "amount_usd",
                     "channel", "status")
MONTHLY_COLUMNS = ("channel", "month", "completed_orders", "revenue_usd")
OUTCOME_COLUMNS = ("source_file", "source_line", "order_id", "outcome", "issue_codes", "corrected_fields")
APPLIED_COLUMNS = ("correction_id", "source_file", "source_line", "order_id", "field", "rule", "original_value",
                   "suggested_value", "decision", "status", "final_value", "row_outcome")
AUDIT_COLUMNS = ("run_id", "source_file", "action", "reason", "sha256", "bytes", "data_rows")

_JOIN = "FROM rows AS r JOIN outcomes AS o ON o.file = r.file AND o.line = r.line"


def write_clean(state: State, out: Path) -> int:
    rows = state.query(f"""SELECT r.order_id, r.customer_id, r.order_date, r.sku, r.quantity, r.amount_cents, r.channel,
        r.status, r.file, r.line {_JOIN} WHERE o.outcome = 'ACCEPTED' ORDER BY r.order_id, r.file, r.line""")
    return write_csv(out / CLEAN, CLEAN_COLUMNS,
                     ((o, c, d, s, q, format_cents(a), ch, st, f, ln) for o, c, d, s, q, a, ch, st, f, ln in rows))


def write_rejected(state: State, out: Path) -> Counter:
    issues: Counter = Counter()

    def rows():
        for row in state.query(f"""SELECT file, line, order_id, issues, {', '.join(f'raw_{c}' for c in COLUMNS)}, raw_extra
                                   FROM rows WHERE fingerprint IS NULL ORDER BY file, line"""):
            issues.update(row[3].split("|"))
            yield row

    write_csv(out / REJECTED, REJECTED_COLUMNS, rows())
    return issues


def write_duplicates(state: State, out: Path, rules: Rules) -> dict[str, int]:
    stats = Counter()
    fields = rules.compare_fields
    shown = ("customer_id", "order_date", "sku", "quantity", "amount_cents", "channel", "status")
    query = state.query(f"""SELECT r.order_id, g.occurrences, g.versions, o.outcome, r.file, r.line, r.fingerprint,
        {', '.join('r.' + c for c in shown)}
        {_JOIN} JOIN groups AS g ON g.order_id = r.order_id
        WHERE g.occurrences > 1 ORDER BY r.order_id, r.file, r.line""")

    def rows():
        for order_id, group in groupby(query, key=lambda r: r[0]):
            members = list(group)
            numbers: dict[str, int] = {}
            for m in members:
                numbers.setdefault(m[6], len(numbers) + 1)
            values = [fp.split("\x1f") for fp in numbers]
            differing = "|".join(f for i, f in enumerate(fields) if len({v[i] for v in values}) > 1)
            versions = members[0][2]
            classification = codes.EXACT_DUPLICATE if versions == 1 else codes.CONFLICTING_DUPLICATE
            stats["exact_duplicate_order_ids" if versions == 1 else "conflicting_order_ids"] += 1
            for m in members:
                stats[m[3]] += 1
                customer, day, sku, qty, cents, channel, status = m[7:]
                yield (order_id, classification, m[3], m[1], versions, numbers[m[6]], differing, m[4], m[5],
                       customer, day, sku, qty, format_cents(cents), channel, status)

    write_csv(out / DUPLICATES, DUPLICATE_COLUMNS, rows())
    return dict(stats)


def write_monthly(state: State, out: Path, rules: Rules) -> list[tuple[str, str, int, str]]:
    marks = ", ".join("?" * len(rules.revenue_statuses))
    rows = [(channel, month, count, format_cents(cents)) for channel, month, count, cents in state.query(
        f"""SELECT r.channel, substr(r.order_date, 1, 7) AS month, COUNT(*), SUM(r.amount_cents) {_JOIN}
            WHERE o.outcome = 'ACCEPTED' AND r.status IN ({marks}) GROUP BY r.channel, month ORDER BY r.channel, month""",
        rules.revenue_statuses)] if rules.revenue_statuses else []
    write_csv(out / MONTHLY, MONTHLY_COLUMNS, rows)
    return rows


def write_row_outcomes(state: State, out: Path) -> int:
    rows = state.query(f"SELECT r.file, r.line, r.order_id, o.outcome, r.issues, r.corrected {_JOIN} ORDER BY r.file, r.line")
    return write_csv(out / ROW_OUTCOMES, OUTCOME_COLUMNS, rows)


def write_corrections(state: State, out: Path, decisions: dict[str, cr.Decision]) -> dict[str, Any]:
    review = list(state.query("""SELECT correction_id, file, line, order_id, field, rule, original, suggested, decision,
        reviewer, note FROM suggestions ORDER BY file, line, field"""))
    write_csv(out / CORRECTION_REVIEW, cr.REVIEW_COLUMNS, review)

    outcomes = dict(((f, ln), o) for f, ln, o in state.query(
        "SELECT o.file, o.line, o.outcome FROM suggestions AS s JOIN outcomes AS o ON o.file = s.file AND o.line = s.line"))
    status_of = {cr.APPROVE: cr.APPLIED, cr.REJECT: cr.REJECTED_BY_REVIEWER, "": cr.PENDING}
    stats: Counter = Counter()
    by_rule: Counter = Counter()
    applied = []
    for cid, file, line, order_id, field, rule, original, suggested, decision, _reviewer, _note in review:
        status = status_of[decision]
        stats[status] += 1
        by_rule[rule] += 1
        applied.append((cid, file, line, order_id, field, rule, original, suggested, decision, status,
                        suggested if status == cr.APPLIED else original, outcomes[(file, line)]))
    known = {row[0] for row in review}
    for cid in sorted(set(decisions) - known):
        d = decisions[cid]
        stats[cr.STALE] += 1
        applied.append((cid, d.source_file, "", "", "", "", "", "", d.decision, cr.STALE, "", ""))
    write_csv(out / CORRECTIONS_APPLIED, APPLIED_COLUMNS, applied)
    return {"suggested": len(review), "applied": stats[cr.APPLIED], "rejected": stats[cr.REJECTED_BY_REVIEWER],
            "pending": stats[cr.PENDING], "stale": stats[cr.STALE], "by_rule": dict(sorted(by_rule.items()))}


def write_processing_audit(out: Path, run_id: int, actions: list[dict]) -> None:
    write_csv(out / PROCESSING_AUDIT, AUDIT_COLUMNS,
              ((run_id, a["file"], a["action"], a["reason"], a["sha256"], a["bytes"], a["data_rows"]) for a in actions))


def outcome_counts(state: State) -> dict[str, int]:
    counts = dict(state.query("SELECT outcome, COUNT(*) FROM outcomes GROUP BY outcome"))
    return {o.lower(): int(counts.get(o, 0)) for o in codes.OUTCOMES}


def normalization_counts(state: State) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for field, kind, count in state.query("SELECT field, kind, SUM(count) FROM normalizations GROUP BY field, kind "
                                          "ORDER BY field, kind"):
        result.setdefault(field, {})[kind] = int(count)
    return result


# Reconciliation -----------------------------------------------------------------------------------------------

def run_checks(state: State, out: Path, rules: Rules, reference: Reference, counts: dict[str, int],
               outcome_rows_written: int) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})

    staged = state.scalar("SELECT COUNT(*) FROM rows")
    declared = state.scalar("SELECT SUM(data_rows) FROM files")
    total = sum(counts.values())
    parts = " + ".join(f"{counts[k]:,} {k.replace('_', ' ')}" for k in counts if counts[k] or k in ("accepted", "rejected"))
    check("row_accounting", declared == staged == total, f"{declared:,} input rows = {parts}")

    mismatched = [name for name, declared_rows, actual in state.query(
        "SELECT f.name, f.data_rows, (SELECT COUNT(*) FROM rows AS r WHERE r.file = f.name) FROM files AS f ORDER BY f.name")
        if declared_rows != actual]
    check("file_row_counts", not mismatched,
          "every staged file holds the rows read from it" if not mismatched else f"row counts differ for {mismatched}")
    check("row_outcomes_complete", outcome_rows_written == staged,
          f"{outcome_rows_written:,} rows in {ROW_OUTCOMES} for {staged:,} input rows")

    ids, clean_rows, revenue, revenue_orders, wrong = set(), 0, Decimal("0.00"), 0, []
    with (out / CLEAN).open(encoding=CSV_ENCODING, newline="") as handle:
        for row in csv.DictReader(handle):
            clean_rows += 1
            ids.add(row["order_id"])
            amount, qty = Decimal(row["amount_usd"]), int(row["quantity"])
            price = reference.prices.get(row["sku"])
            if price is None or abs(amount - (price * qty).quantize(Decimal("0.01"))) > rules.tolerance \
                    or qty < rules.quantity_min:
                wrong.append(row["order_id"])
            if row["status"] in rules.revenue_statuses:
                revenue += amount
                revenue_orders += 1
    check("clean_order_ids_unique", clean_rows == len(ids) == counts["accepted"],
          f"{clean_rows:,} clean rows, {len(ids):,} distinct order IDs, {counts['accepted']:,} accepted")
    check("clean_amounts_match_prices", not wrong,
          "every clean amount equals unit price x quantity within the tolerance" if not wrong
          else f"{len(wrong)} clean rows fail the price check, first {wrong[:5]}")

    monthly_total, monthly_orders = Decimal("0.00"), 0
    with (out / MONTHLY).open(encoding=CSV_ENCODING, newline="") as handle:
        for row in csv.DictReader(handle):
            monthly_total += Decimal(row["revenue_usd"])
            monthly_orders += int(row["completed_orders"])
    check("revenue_reconciles", monthly_total == revenue and monthly_orders == revenue_orders,
          f"monthly report {monthly_orders:,} orders / {monthly_total:,.2f} {rules.currency}; clean detail "
          f"{revenue_orders:,} orders / {revenue:,.2f} {rules.currency}")
    return checks


def quality_report(rules: Rules, reference: Reference, counts: dict[str, int], issues: Counter,
                   duplicates: dict[str, int], monthly: list, corrections: dict[str, Any],
                   normalizations: dict[str, dict[str, int]], checks: list[dict[str, str]],
                   inputs: list[dict[str, Any]]) -> dict[str, Any]:
    revenue_orders = sum(r[2] for r in monthly)
    revenue = sum((Decimal(r[3]) for r in monthly), Decimal("0.00"))
    statuses = ", ".join(rules.revenue_statuses) or "no"
    policy = ("all versions of conflicting order IDs are quarantined from clean data"
              if rules.conflict_policy == "quarantine_all"
              else "the first version of a conflicting order ID (by file name and line) is kept; the others are "
                   "reported as overridden")
    return {
        "report_version": 1,
        "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL",
        "rows": {"input": sum(counts.values()), **counts},
        "revenue": {"currency": rules.currency, "statuses": list(rules.revenue_statuses), "orders": revenue_orders,
                    "amount": f"{revenue:.2f}"},
        "issues": dict(sorted(issues.items())),
        "duplicates": {
            "policy": rules.conflict_policy,
            "exact_duplicate_order_ids": duplicates.get("exact_duplicate_order_ids", 0),
            "conflicting_order_ids": duplicates.get("conflicting_order_ids", 0),
            "duplicate_rows_ignored": counts["duplicate_ignored"],
            "quarantined_rows": counts["quarantined_conflict"],
            "overridden_rows": counts["conflict_overridden"],
        },
        "normalizations": normalizations,
        "corrections": corrections,
        "checks": checks,
        "inputs": inputs,
        "rules": {"source": rules.source, "sha256": rules.sha256, "conflict_policy": rules.conflict_policy,
                  "amount_tolerance": str(rules.tolerance), "date_formats": list(rules.date_formats)},
        "reference": {"sha256": reference.sha256, "products": len(reference.prices),
                      "channels": list(reference.channel_names)},
        "notes": [
            f"Only {statuses} orders count toward revenue.",
            f"Exact duplicates are ignored; {policy}.",
            f"No currency conversion; all amounts are {rules.currency}.",
            "Revenue is the gross order amount, before taxes or fees.",
        ],
    }
