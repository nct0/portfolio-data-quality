"""Assisted correction: suggestions that a reviewer must approve before they change any data.

A suggestion is only made for a rejected row, only for SKU, status, channel or date, and only when exactly
one candidate exists. Order IDs, customer IDs, quantities, amounts and prices are never suggested.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from order_quality import errors as codes
from order_quality.config import Rules, alias_key
from order_quality.errors import InputError
from order_quality.normalize import parse_date
from order_quality.reference import Reference
from order_quality.validate import CheckedRow

SKU_FORMAT = "sku_format"
STATUS_TYPO = "status_typo"
CHANNEL_TYPO = "channel_typo"
DATE_FORMAT = "date_format"

APPROVE = "APPROVE"
REJECT = "REJECT"
DECISIONS = (APPROVE, REJECT)

# Status of each suggestion in corrections_applied.csv.
APPLIED = "APPLIED"
REJECTED_BY_REVIEWER = "REJECTED"
PENDING = "PENDING"
STALE = "STALE"

REVIEW_COLUMNS = ("correction_id", "source_file", "source_line", "order_id", "field", "rule", "original_value",
                  "suggested_value", "decision", "reviewer", "note")
REQUIRED_DECISION_COLUMNS = ("correction_id", "source_file", "decision")


@dataclass(frozen=True)
class Suggestion:
    correction_id: str
    field: str
    rule: str
    original: str
    suggested: str


@dataclass(frozen=True)
class Decision:
    correction_id: str
    source_file: str
    decision: str
    reviewer: str
    note: str


def correction_id(source_file: str, line: int, field: str, original: str, suggested: str) -> str:
    """Stable ID: the same suggestion on the same unchanged source row always gets the same ID."""
    key = "\x1f".join((source_file, str(line), field, original, suggested)).encode("utf-8")
    return "CR-" + hashlib.sha256(key).hexdigest()[:16]


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


class Suggester:
    def __init__(self, rules: Rules, reference: Reference) -> None:
        self.rules = rules
        skus: dict[str, list[str]] = {}
        for sku in reference.prices:
            skus.setdefault(_sku_key(sku), []).append(sku)
        self.skus = {k: v[0] for k, v in skus.items() if len(v) == 1}
        self.statuses = _candidates(rules.statuses, rules.status_aliases)
        self.channels = _candidates(reference.channel_names, rules.channel_aliases)

    def suggest(self, source_file: str, line: int, raw: dict[str, str], row: CheckedRow) -> list[Suggestion]:
        rules, found = self.rules, []
        if codes.UNKNOWN_SKU in row.issues and rules.suggest_sku_format:
            sku = self.skus.get(_sku_key(raw["sku"]))
            if sku and raw["sku"]:
                found.append(("sku", SKU_FORMAT, sku))
        if codes.UNKNOWN_STATUS in row.issues and rules.suggest_typos:
            status = self._typo(raw["status"], self.statuses)
            if status:
                found.append(("status", STATUS_TYPO, status))
        if codes.UNKNOWN_CHANNEL in row.issues and rules.suggest_typos:
            channel = self._typo(raw["channel"], self.channels)
            if channel:
                found.append(("channel", CHANNEL_TYPO, channel))
        if codes.INVALID_DATE in row.issues and rules.suggest_date_formats:
            value = raw["order_date"].strip()
            dates = {d for fmt in rules.suggest_date_formats if (d := parse_date(value, (fmt,)))}
            if len(dates) == 1:
                found.append(("order_date", DATE_FORMAT, dates.pop()))
        return [Suggestion(correction_id(source_file, line, field, raw[field], suggested), field, rule, raw[field], suggested)
                for field, rule, suggested in found]

    def _typo(self, value: str, candidates: dict[str, str]) -> str | None:
        key = alias_key(value)
        if len(key) < self.rules.min_typo_length:
            return None
        distances: dict[str, int] = {}
        for candidate, target in candidates.items():
            d = levenshtein(key, candidate)
            if d <= self.rules.max_typo_distance:
                distances[target] = min(d, distances.get(target, d))
        if not distances:
            return None
        best = min(distances.values())
        winners = [t for t, d in distances.items() if d == best]
        return winners[0] if len(winners) == 1 else None


def _sku_key(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def _candidates(names: tuple[str, ...], aliases: dict[str, str]) -> dict[str, str]:
    result = {alias_key(n): n for n in names}
    result.update(aliases)
    return result


def load_decisions(path: Path | None) -> dict[str, Decision]:
    """Read a reviewer's decision file. Rows with an empty decision are pending and ignored."""
    if path is None:
        return {}
    if not path.is_file():
        raise InputError("IN006", f"decision file '{path}' not found. Pass the edited correction_review.csv with --corrections.")
    decisions: dict[str, Decision] = {}
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [c for c in REQUIRED_DECISION_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise InputError("IN006", f"'{path.name}' is missing column(s) {', '.join(missing)}. "
                                          "Start from correction_review.csv and edit only the decision, reviewer and note columns.")
            for line, row in enumerate(reader, start=2):
                decision = (row.get("decision") or "").strip().upper()
                if not decision:
                    continue
                if decision not in DECISIONS:
                    raise InputError("IN006", f"'{path.name}' line {line}: decision '{row['decision']}' is not allowed; "
                                              "use APPROVE, REJECT or leave it empty.")
                cid = (row.get("correction_id") or "").strip()
                if not re.fullmatch(r"CR-[0-9a-f]{16}", cid):
                    raise InputError("IN006", f"'{path.name}' line {line}: correction_id '{cid}' is not a valid ID; "
                                              "copy the rows from correction_review.csv without changing the ID.")
                entry = Decision(cid, (row.get("source_file") or "").strip(), decision,
                                 (row.get("reviewer") or "").strip(), (row.get("note") or "").strip())
                previous = decisions.get(cid)
                if previous and previous.decision != decision:
                    raise InputError("IN006", f"'{path.name}' line {line}: {cid} is both approved and rejected.")
                decisions[cid] = entry
    except UnicodeDecodeError as exc:
        raise InputError("IN006", f"'{path.name}' is not UTF-8 text: {exc}") from exc
    return decisions


def decisions_digest(decisions: dict[str, Decision], source_file: str) -> str:
    """Fingerprint of the decisions that target one input file (part of its staging key)."""
    items = sorted((d.correction_id, d.decision) for d in decisions.values() if d.source_file == source_file)
    return hashlib.sha256(repr(items).encode("utf-8")).hexdigest()
