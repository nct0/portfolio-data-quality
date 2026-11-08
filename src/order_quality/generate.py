"""Deterministic synthetic order exports. All data is fictional.

Scenarios:
- baseline:   the original 5,020-row case study, byte-identical to data/raw and data/reference.
- scale:      any number of rows over several files, streamed, with a manifest of the exact expected results.
- late-batch: a "day 2" export that re-sends, contradicts and misspells earlier orders, plus a demo
              reviewer decision file.
"""

from __future__ import annotations

import csv
import datetime
import json
import random
from collections import Counter
from decimal import Decimal
from pathlib import Path

from order_quality.corrections import correction_id
from order_quality.normalize import format_cents

HEADERS = ["order_id", "customer_id", "order_date", "sku", "quantity", "amount_usd", "channel", "status"]
PRODUCTS = [("SKU-001", "Notebook", 12.50), ("SKU-002", "Desk Lamp", 35.00), ("SKU-003", "USB Cable", 7.25),
            ("SKU-004", "Travel Mug", 18.00), ("SKU-005", "Backpack", 49.90), ("SKU-006", "Keyboard", 63.00),
            ("SKU-007", "Mouse", 22.00), ("SKU-008", "Phone Stand", 11.00)]
CHANNELS = [["Web", "North"], ["Marketplace", "Central"], ["Retail", "South"]]
BASELINE_SEED = 20261108
LATE_BATCH_SEED = 20261109
MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _dmy(day: datetime.date) -> str:
    """'%d-%b-%Y' with English month names on every locale."""
    return f"{day.day:02d}-{MONTH_ABBR[day.month - 1]}-{day.year}"


def _save(path: Path, headers: list[str], rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def write_reference(folder: Path) -> None:
    _save(folder / "products.csv", ["sku", "product_name", "unit_price_usd"], PRODUCTS)
    _save(folder / "channels.csv", ["channel", "region"], CHANNELS)


# Baseline -----------------------------------------------------------------------------------------------------

def baseline_rows() -> tuple[list[list[str]], list[list[list[str]]]]:
    """The original algorithm, call for call: returns (base orders before corruption copies, rows per file)."""
    rng = random.Random(BASELINE_SEED)
    rows = []
    for n in range(1, 5001):
        sku, _name, price = rng.choice(PRODUCTS)
        qty = rng.randint(1, 5)
        order_date = datetime.date(2026, 1, 1) + datetime.timedelta(days=rng.randint(0, 180))
        date_style = rng.choice(["iso", "us", "dmy"])
        date_value = (order_date.isoformat() if date_style == "iso"
                      else order_date.strftime("%m/%d/%Y") if date_style == "us" else _dmy(order_date))
        channel = rng.choice(["Web", "Marketplace", "Retail"])
        status = rng.choices(["Completed", "Refunded", "Pending"], weights=[85, 8, 7])[0]
        amt = round(price * qty, 2)
        customer = f"C-{rng.randint(1, 900):04d}"
        sku_value = rng.choice([sku, sku.lower(), f" {sku} "])
        channel_value = rng.choice([channel, channel.lower(), f" {channel} "])
        status_value = rng.choice([status, status.lower(), f" {status} "])
        rows.append([f"ORD-{n:06d}", customer, date_value, sku_value, str(qty), f"{amt:.2f}", channel_value, status_value])
    clean = [list(r) for r in rows]
    # Controlled flaws: each affects one unique source record.
    by_id = {r[0]: r for r in rows}
    for i in range(11, 21):
        by_id[f"ORD-{i:06d}"][5] = "9999.99"  # amount mismatch
    for i in range(21, 31):
        by_id[f"ORD-{i:06d}"][4] = "0"  # invalid quantity
    for i in range(31, 41):
        by_id[f"ORD-{i:06d}"][3] = "SKU-INVALID"
    for i in range(41, 51):
        by_id[f"ORD-{i:06d}"][2] = "not-a-date"
    for i in range(51, 61):
        by_id[f"ORD-{i:06d}"][6] = "UNKNOWN"
    for i in range(61, 71):
        by_id[f"ORD-{i:06d}"][7] = "MISSING"
    for i in range(71, 81):
        by_id[f"ORD-{i:06d}"][1] = ""  # missing customer
    for i in range(81, 91):
        by_id[f"ORD-{i:06d}"][5] = "n/a"  # invalid amount
    for i in range(91, 101):
        by_id[f"ORD-{i:06d}"][0] = ""  # missing order ID
    # Orders 101-115 are sent twice unchanged; orders 116-120 a second time with another customer ID (conflicts).
    rows.extend([list(rows[i]) for i in range(100, 115)])
    for i in range(115, 120):
        duplicate = list(rows[i])
        duplicate[1] = "C-9999"
        rows.append(duplicate)
    rng.shuffle(rows)
    return clean, [rows[j::3] for j in range(3)]


def generate_baseline(out: Path) -> dict:
    write_reference(out / "reference")
    _clean, files = baseline_rows()
    for j, rows in enumerate(files, start=1):
        _save(out / "raw" / f"orders_export_{j}.csv", HEADERS, rows)
    return {"scenario": "baseline", "files": len(files), "rows": sum(len(f) for f in files)}


# Scale ---------------------------------------------------------------------------------------------------------

_FLAWS = ("AMOUNT_MISMATCH", "INVALID_QUANTITY", "UNKNOWN_SKU", "INVALID_DATE", "UNKNOWN_CHANNEL", "UNKNOWN_STATUS",
          "MISSING_CUSTOMER_ID", "INVALID_AMOUNT", "MISSING_ORDER_ID")
_TYPOS = {"Completed": "Complted", "Pending": "Pendng", "Refunded": "Refundd",
          "Web": "Webb", "Marketplace": "Marketplce", "Retail": "Retal"}
_CHANNEL_ALIASES = {"Web": "online", "Retail": "in-store"}
_PRICE_CENTS = {sku: int(Decimal(str(price)) * 100) for sku, _n, price in PRODUCTS}


def _messy(rng: random.Random, value: str) -> str:
    return rng.choice([value, value.lower(), f" {value} "])


def _scale_order(rng: random.Random, n: int, customers: int) -> dict:
    sku = rng.choice(PRODUCTS)[0]
    qty = rng.randint(1, 5)
    day = datetime.date(2026, 1, 1) + datetime.timedelta(days=rng.randint(0, 364))
    channel = rng.choice(["Web", "Marketplace", "Retail"])
    status = rng.choices(["Completed", "Refunded", "Pending"], weights=[85, 8, 7])[0]
    return {"order_id": f"ORD-{n:07d}", "customer_id": f"C-{rng.randint(1, customers):05d}", "day": day,
            "date_style": rng.choice(["iso", "us", "dmy"]), "sku": sku, "qty": qty, "channel": channel, "status": status}


def _render(rng: random.Random, order: dict) -> list[str]:
    day = order["day"]
    date_value = {"iso": day.isoformat(), "us": day.strftime("%m/%d/%Y"), "dmy": _dmy(day)}[order["date_style"]]
    channel = order["channel"]
    if channel in _CHANNEL_ALIASES and rng.random() < 0.02:
        channel_value = _CHANNEL_ALIASES[channel]
    else:
        channel_value = _messy(rng, channel)
    amount = format_cents(_PRICE_CENTS[order["sku"]] * order["qty"])
    return [order["order_id"], order["customer_id"], date_value, _messy(rng, order["sku"]), str(order["qty"]), amount,
            channel_value, _messy(rng, order["status"])]


def generate_scale(out: Path, rows: int, files: int, seed: int) -> dict:
    """Stream `rows` base orders into `files` exports. Returns (and writes) the manifest with exact expectations."""
    if rows < 1 or files < 1:
        raise ValueError("rows and files must be at least 1")
    write_reference(out / "reference")
    rng = random.Random(seed)
    customers = max(900, rows // 5)
    width = max(2, len(str(files)))
    paths = [out / "raw" / f"orders_export_{i:0{width}d}.csv" for i in range(1, files + 1)]
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    handles = [p.open("w", newline="", encoding="utf-8-sig") for p in paths]
    writers = [csv.writer(h) for h in handles]
    file_rows = [0] * files
    expected = Counter()
    issues: Counter = Counter()
    suggestions: Counter = Counter()
    revenue_cents = 0

    def emit(index: int, row: list[str]) -> None:
        writers[index].writerow(row)
        file_rows[index] += 1
        expected["input"] += 1

    def other_file(index: int) -> int:
        return index if files == 1 else (index + 1 + rng.randrange(files - 1)) % files

    try:
        for writer in writers:
            writer.writerow(HEADERS)
        for n in range(1, rows + 1):
            order = _scale_order(rng, n, customers)
            row = _render(rng, order)
            home = rng.randrange(files)
            draw = rng.random()
            accepted = True
            if draw < 0.018:  # one invalid value, exactly one issue code
                flaw = _FLAWS[rng.randrange(len(_FLAWS))]
                column, value = {"AMOUNT_MISMATCH": (5, "9999.99"), "INVALID_QUANTITY": (4, "0"),
                                 "UNKNOWN_SKU": (3, "SKU-INVALID"), "INVALID_DATE": (2, "not-a-date"),
                                 "UNKNOWN_CHANNEL": (6, "UNKNOWN"), "UNKNOWN_STATUS": (7, "MISSING"),
                                 "MISSING_CUSTOMER_ID": (1, ""), "INVALID_AMOUNT": (5, "n/a"),
                                 "MISSING_ORDER_ID": (0, "")}[flaw]
                row[column] = value
                issues[flaw] += 1
                expected["rejected"] += 1
                emit(home, row)
                continue
            if draw < 0.021:  # a value a reviewer could correct: rejected until approved
                rule = ("sku_format", "status_typo", "channel_typo", "date_format")[rng.randrange(4)]
                if rule == "sku_format":
                    row[3] = order["sku"].replace("-", "") if rng.random() < 0.5 else order["sku"].replace("-", " ").lower()
                    issues["UNKNOWN_SKU"] += 1
                elif rule == "status_typo":
                    row[7] = _TYPOS[order["status"]]
                    issues["UNKNOWN_STATUS"] += 1
                elif rule == "channel_typo":
                    row[6] = _TYPOS[order["channel"]]
                    issues["UNKNOWN_CHANNEL"] += 1
                else:
                    row[2] = order["day"].strftime("%Y/%m/%d")
                    issues["INVALID_DATE"] += 1
                suggestions[rule] += 1
                expected["rejected"] += 1
                emit(home, row)
                continue
            emit(home, row)
            if draw < 0.024:  # the same order again in another export, formatted differently
                emit(other_file(home), _render(rng, order))
                expected["duplicate_ignored"] += 1
            elif draw < 0.0253:  # contradicting versions: two, or three for one in ten
                versions = 3 if draw >= 0.025 else 2
                changed = dict(order, qty=order["qty"] % 5 + 1)
                emit(other_file(home), _render(rng, changed))
                if versions == 3:
                    emit(other_file(home), _render(rng, dict(order, customer_id="C-99999")))
                expected["quarantined_conflict"] += versions
                expected["conflicting_order_ids"] += 1
                accepted = False
            if accepted:
                expected["accepted"] += 1
                if order["status"] == "Completed":
                    expected["revenue_orders"] += 1
                    revenue_cents += _PRICE_CENTS[order["sku"]] * order["qty"]
    finally:
        for handle in handles:
            handle.close()

    manifest = {
        "scenario": "scale", "seed": seed, "base_orders": rows,
        "files": [{"name": p.name, "rows": r} for p, r in zip(paths, file_rows)],
        "expected": {
            "rows": {k: expected[k] for k in ("input", "accepted", "rejected", "duplicate_ignored", "quarantined_conflict")},
            "conflicting_order_ids": expected["conflicting_order_ids"],
            "revenue_orders": expected["revenue_orders"],
            "revenue_usd": format_cents(revenue_cents),
            "issues": dict(sorted(issues.items())),
            "suggestions": dict(sorted(suggestions.items())),
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


# Late batch ----------------------------------------------------------------------------------------------------

LATE_FILE = "orders_export_4.csv"


def generate_late_batch(out: Path, decisions_dir: Path) -> dict:
    """Day 2: one more export (written to out/late-batch) and a reviewer's decision file (decisions_dir)."""
    rng = random.Random(LATE_BATCH_SEED)
    clean, _files = baseline_rows()
    baseline = {r[0]: r for r in clean}
    prices = {sku: Decimal(str(price)) for sku, _n, price in PRODUCTS}
    rows: list[tuple[list[str], dict]] = []  # (raw row, facts used for the manifest and the decisions)

    def iso(value: str) -> str:
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                pass
        day, month, year = value.split("-")
        return datetime.date(int(year), MONTH_ABBR.index(month) + 1, int(day)).isoformat()

    def amount(sku: str, qty: int) -> str:
        return f"{prices[sku] * qty:.2f}"

    new_orders = []
    for n in range(5001, 5241):
        sku = rng.choice(PRODUCTS)[0]
        qty = rng.randint(1, 5)
        day = datetime.date(2026, 7, 1) + datetime.timedelta(days=rng.randint(0, 30))
        channel = rng.choice(["Web", "Marketplace", "Retail"])
        status = rng.choices(["Completed", "Refunded", "Pending"], weights=[85, 8, 7])[0]
        channel_value = rng.choice([channel, channel.lower(), f" {channel} ", {"Web": "online", "Retail": "in-store",
                                                                                "Marketplace": "MARKETPLACE"}[channel]])
        status_value = "complete" if status == "Completed" and rng.random() < 0.1 else rng.choice([status, status.upper()])
        row = [f"ORD-{n:06d}", f"C-{rng.randint(1, 900):04d}", rng.choice([day.isoformat(), day.strftime("%m/%d/%Y")]),
               rng.choice([sku, sku.lower()]), str(qty), amount(sku, qty), channel_value, status_value]
        new_orders.append((row, {"kind": "new", "sku": sku, "qty": qty, "status": status}))
    rows.extend(new_orders)
    for row, _facts in new_orders[:2]:  # the same new order twice in this export
        copy = list(row)
        copy[3] = copy[3].upper()
        rows.append((copy, {"kind": "duplicate_in_batch"}))

    # Values a reviewer can correct (each row has exactly one issue).
    candidates = [("sku", "SKU007", "SKU-007"), ("sku", "sku 003", "SKU-003"), ("sku", "SKU_005", "SKU-005"),
                  ("sku", "SKU002", "SKU-002"), ("sku", "SKU.008", "SKU-008"), ("sku", "SKU 001", "SKU-001"),
                  ("status", "Complted", "Completed"), ("status", "Pendng", "Pending"), ("status", "Refundd", "Refunded"),
                  ("status", "Compleeted", "Completed"),
                  ("channel", "Retal", "Retail"), ("channel", "Marketplce", "Marketplace"), ("channel", "Webb", "Web"),
                  ("order_date", "2026/07/14", "2026-07-14"), ("order_date", "Jul 21, 2026", "2026-07-21"),
                  ("order_date", "2026/07/02", "2026-07-02"), ("order_date", "28 Jul 2026", "2026-07-28")]
    rule_of = {"sku": "sku_format", "status": "status_typo", "channel": "channel_typo", "order_date": "date_format"}
    for i, (field, original, suggested) in enumerate(candidates):
        n = 5301 + i
        sku = suggested if field == "sku" else rng.choice(PRODUCTS)[0]
        qty = rng.randint(1, 5)
        status = suggested if field == "status" else "Completed"
        row = [f"ORD-{n:06d}", f"C-{rng.randint(1, 900):04d}", "2026-07-10", sku, str(qty), amount(sku, qty),
               "Web", status]
        row[HEADERS.index(field)] = original
        rows.append((row, {"kind": "suggestion", "field": field, "original": original, "suggested": suggested,
                           "rule": rule_of[field], "sku": sku, "qty": qty, "status": status}))

    # Values that cannot be repaired without the client.
    unrepairable = [("order_date", "07/03/26", "INVALID_DATE"), ("sku", "SKU-999", "UNKNOWN_SKU"),
                    ("quantity", "2.5", "INVALID_QUANTITY"), ("amount_usd", "$45.00", "INVALID_AMOUNT"),
                    ("status", "Shipped", "UNKNOWN_STATUS"), ("channel", "Phone", "UNKNOWN_CHANNEL")]
    for i, (field, value, issue) in enumerate(unrepairable):
        n = 5401 + i
        sku = "SKU-004"
        row = [f"ORD-{n:06d}", f"C-{rng.randint(1, 900):04d}", "2026-07-12", sku, "2", amount(sku, 2), "Retail", "Pending"]
        row[HEADERS.index(field)] = value
        rows.append((row, {"kind": "unrepairable", "issue": issue}))

    # Orders from the first exports, sent again.
    for order_id in ("ORD-000500", "ORD-000501", "ORD-000502", "ORD-000503", "ORD-000504"):
        base = baseline[order_id]
        resent = [base[0], base[1], iso(base[2]), base[3].strip().upper(), base[4], base[5], base[6].strip().upper(),
                  base[7].strip()]
        rows.append((resent, {"kind": "resent_exact", "order_id": order_id}))
    for order_id in ("ORD-000600", "ORD-000601"):
        base = baseline[order_id]
        sku = base[3].strip().upper()
        qty = int(base[4]) % 5 + 1
        changed = [base[0], base[1], iso(base[2]), sku, str(qty), amount(sku, qty), base[6].strip(), base[7].strip()]
        rows.append((changed, {"kind": "resent_conflict", "order_id": order_id,
                               "baseline_status": base[7].strip().title(), "baseline_amount": base[5]}))
    base = baseline["ORD-000116"]  # already conflicting in the baseline: a third version
    sku = base[3].strip().upper()
    qty = int(base[4]) % 5 + 1
    rows.append(([base[0], base[1], iso(base[2]), sku, str(qty), amount(sku, qty), base[6].strip(), base[7].strip()],
                 {"kind": "third_version", "order_id": "ORD-000116"}))

    rng.shuffle(rows)
    batch_dir = out / "late-batch"
    _save(batch_dir / LATE_FILE, HEADERS, [r for r, _f in rows])

    # The reviewer's decisions: approve most suggestions, reject one, leave two open.
    notes = {"sku_format": "SKU written without the standard hyphen", "status_typo": "obvious spelling mistake",
             "channel_typo": "obvious spelling mistake", "date_format": "unambiguous year-first or month-name date"}
    rejected_value, pending_values = "Compleeted", {"Webb", "28 Jul 2026"}
    decision_rows = []
    approved_cents = 0
    approved = 0
    for line, (row, facts) in enumerate(rows, start=2):
        if facts["kind"] != "suggestion":
            continue
        cid = correction_id(LATE_FILE, line, facts["field"], facts["original"], facts["suggested"])
        if facts["original"] == rejected_value:
            decision, note = "REJECT", "Not confirmed by the client; keep for manual review"
        elif facts["original"] in pending_values:
            decision, note = "", ""
        else:
            decision, note = "APPROVE", notes[facts["rule"]]
            approved += 1
            if facts["status"] == "Completed":
                approved_cents += int(prices[facts["sku"]] * facts["qty"] * 100)
        decision_rows.append([cid, LATE_FILE, str(line), row[0], facts["field"], facts["rule"], facts["original"],
                              facts["suggested"], decision, "ops-reviewer" if decision else "", note])
    _save(decisions_dir / "demo-decisions.csv",
          ["correction_id", "source_file", "source_line", "order_id", "field", "rule", "original_value",
           "suggested_value", "decision", "reviewer", "note"], decision_rows)

    kinds = Counter(f["kind"] for _r, f in rows)
    new_completed = sum(int(prices[f["sku"]] * f["qty"] * 100) for _r, f in rows
                        if f["kind"] == "new" and f["status"] == "Completed")
    lost = sum(int(Decimal(f["baseline_amount"]) * 100) for _r, f in rows
               if f["kind"] == "resent_conflict" and f["baseline_status"] == "Completed")
    new_completed_orders = sum(1 for _r, f in rows if f["kind"] == "new" and f["status"] == "Completed")
    lost_orders = sum(1 for _r, f in rows if f["kind"] == "resent_conflict" and f["baseline_status"] == "Completed")
    approved_completed = sum(1 for _r, f in rows if f["kind"] == "suggestion" and f["status"] == "Completed"
                             and f["original"] != rejected_value and f["original"] not in pending_values)
    manifest = {
        "scenario": "late-batch", "seed": LATE_BATCH_SEED, "file": LATE_FILE, "rows": len(rows),
        "composition": dict(sorted(kinds.items())),
        "suggestions": dict(sorted(Counter(f["rule"] for _r, f in rows if f["kind"] == "suggestion").items())),
        "demo_decisions": {"approve": approved, "reject": 1, "pending": len(pending_values)},
        "effect_on_baseline": {
            "without_decisions": {
                "input": len(rows),
                "accepted": kinds["new"] - kinds["resent_conflict"],
                "rejected": kinds["suggestion"] + kinds["unrepairable"],
                "duplicate_ignored": kinds["duplicate_in_batch"] + kinds["resent_exact"],
                "quarantined_conflict": 2 * kinds["resent_conflict"] + kinds["third_version"],
                "revenue_orders": new_completed_orders - lost_orders,
                "revenue_usd": format_cents(new_completed - lost),
            },
            "with_demo_decisions": {
                "accepted": kinds["new"] - kinds["resent_conflict"] + approved,
                "rejected": kinds["suggestion"] + kinds["unrepairable"] - approved,
                "revenue_orders": new_completed_orders - lost_orders + approved_completed,
                "revenue_usd": format_cents(new_completed - lost + approved_cents),
            },
        },
    }
    (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest
