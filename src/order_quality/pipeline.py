"""One run: load rules -> scan inputs -> stage new or changed files -> resolve duplicates -> write outputs -> check.

Run lifecycle:
1. Rules, reference data and reviewer decisions are loaded and validated before any file is touched.
2. Every input file is hashed. A file is (re)staged when it is new, its content changed, or the rules, reference
   data or decisions that apply to it changed; otherwise its staged rows are reused. Files that disappeared are
   removed from staging; a file whose content equals an earlier file is skipped.
3. Duplicate resolution, exports and checks always run over all staged rows, so an incremental run produces
   exactly the same business outputs as a full rebuild.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from order_quality import corrections as cr
from order_quality import outputs, workbook
from order_quality.config import COLUMNS, Rules, load_rules
from order_quality.errors import EXIT_CHECK_FAILED, EXIT_OK, InputError
from order_quality.export import append_line, write_json
from order_quality.log import event
from order_quality.reader import file_sha256, read_records
from order_quality.reference import Reference, load_reference
from order_quality.state import NORMALIZATION_VERSION, FileEntry, State
from order_quality.validate import RowChecker, fingerprint

NEW, CHANGED, RESTAGED, UNCHANGED, REMOVED, DUPLICATE = (
    "NEW", "CHANGED", "RESTAGED", "UNCHANGED", "REMOVED", "SKIPPED_DUPLICATE_CONTENT")
STATE_FILE = "state.sqlite"
DEFAULT_BATCH_SIZE = 5000


@dataclass
class RunOptions:
    inputs: list[Path]
    reference: Path
    out: Path
    config: Path | None = None
    corrections: Path | None = None
    state: Path | None = None
    full_rebuild: bool = False
    batch_size: int = DEFAULT_BATCH_SIZE
    workbook: bool = True


@dataclass
class RunResult:
    exit_code: int
    run_id: int
    out: Path
    report: dict[str, Any]
    actions: list[dict[str, Any]]
    seconds: dict[str, float] = field(default_factory=dict)


def discover_inputs(paths: list[Path], pattern: str) -> list[Path]:
    """Input files from files and folders (non-recursive), sorted by file name."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(p for p in path.glob(pattern) if p.is_file())
        elif path.is_file():
            found.append(path)
        else:
            raise InputError("IN001", f"input path '{path}' does not exist.")
    if not found:
        raise InputError("IN002", f"no input files matching '{pattern}' in {', '.join(str(p) for p in paths)}.")
    names = Counter(p.name for p in found)
    clashes = sorted(n for n, c in names.items() if c > 1)
    if clashes:
        raise InputError("IN007", f"several input files are named {', '.join(clashes)}. File names identify sources in "
                                  "every report, so rename one of them.")
    return sorted(found, key=lambda p: p.name)


def run(options: RunOptions) -> RunResult:
    started = time.perf_counter()
    seconds: dict[str, float] = {}
    rules = load_rules(options.config)
    reference = load_reference(options.reference, rules)
    decisions = cr.load_decisions(options.corrections)
    inputs = discover_inputs(options.inputs, rules.file_pattern)
    if options.batch_size < 1:
        raise ValueError("batch size must be at least 1")
    out = options.out
    out.mkdir(parents=True, exist_ok=True)

    state = State.open(options.state or out / STATE_FILE, options.full_rebuild)
    try:
        mode = "full_rebuild" if options.full_rebuild or not state.files() else "incremental"
        run_id = state.begin_run(mode, rules.sha256, reference.sha256)
        event("run started", run_id=run_id, mode=mode, inputs=len(inputs), rules=rules.source)

        t = time.perf_counter()
        actions, inventory = _stage(state, run_id, inputs, rules, reference, decisions, options.batch_size)
        seconds["stage"] = time.perf_counter() - t

        t = time.perf_counter()
        state.resolve(rules.conflict_policy)
        seconds["resolve"] = time.perf_counter() - t

        t = time.perf_counter()
        outputs.write_clean(state, out)
        issues = outputs.write_rejected(state, out)
        duplicates = outputs.write_duplicates(state, out, rules)
        monthly = outputs.write_monthly(state, out, rules)
        outcome_rows = outputs.write_row_outcomes(state, out)
        correction_stats = outputs.write_corrections(state, out, decisions)
        seconds["export"] = time.perf_counter() - t

        t = time.perf_counter()
        counts = outputs.outcome_counts(state)
        checks = outputs.run_checks(state, out, rules, reference, counts, outcome_rows)
        report = outputs.quality_report(rules, reference, counts, issues, duplicates, monthly, correction_stats,
                                        outputs.normalization_counts(state), checks, inventory)
        write_json(out / outputs.QUALITY_REPORT, report)
        outputs.write_processing_audit(out, run_id, actions)
        seconds["checks"] = time.perf_counter() - t
        # All CSV and JSON business outputs are complete at this point; the workbook is built from them.

        if options.workbook:
            t = time.perf_counter()
            workbook.build(out, rules.workbook_max_detail_rows)
            seconds["workbook"] = time.perf_counter() - t

        exit_code = EXIT_OK if report["status"] == "PASS" else EXIT_CHECK_FAILED
        for failed in (c for c in checks if c["status"] != "PASS"):
            event("reconciliation check failed", level=40, check=failed["check"], detail=failed["detail"])
        seconds["total"] = time.perf_counter() - started
        state.finish_run(run_id, exit_code)
    finally:
        state.close()

    append_line(out / outputs.RUN_LOG, {
        "run_id": run_id, "mode": mode, "exit_code": exit_code, "status": report["status"],
        "seconds": {k: round(v, 3) for k, v in seconds.items()},
        "files": dict(sorted(Counter(a["action"] for a in actions).items())),
        "rows": report["rows"], "revenue_usd": report["revenue"]["amount"],
    })
    event("run finished", run_id=run_id, status=report["status"], seconds=round(seconds["total"], 3),
          **{k: v for k, v in report["rows"].items()})
    return RunResult(exit_code, run_id, out, report, actions, seconds)


def _stage(state: State, run_id: int, inputs: list[Path], rules: Rules, reference: Reference,
           decisions: dict[str, cr.Decision], batch_size: int) -> tuple[list[dict], list[dict]]:
    registry = state.files()
    checker = RowChecker(rules, reference)
    suggester = cr.Suggester(rules, reference)
    seen: dict[str, str] = {}  # sha256 -> first file name with that content
    actions: list[dict] = []
    inventory: list[dict] = []

    for path in inputs:
        name, size, sha = path.name, path.stat().st_size, file_sha256(path)
        if sha in seen:
            if name in registry:
                state.drop_file(name)
            rows = state.files()[seen[sha]].data_rows
            actions.append(_action(name, DUPLICATE, f"same content as {seen[sha]}; not ingested twice", sha, size, rows))
            inventory.append({"file": name, "sha256": sha, "bytes": size, "data_rows": rows,
                              "status": DUPLICATE, "duplicate_of": seen[sha]})
            event("file skipped", file=name, reason="duplicate content", duplicate_of=seen[sha])
            continue
        seen[sha] = name
        decisions_sha = cr.decisions_digest(decisions, name)
        key = hashlib.sha256("|".join((sha, rules.sha256, reference.sha256, decisions_sha,
                                       NORMALIZATION_VERSION)).encode()).hexdigest()
        previous = registry.get(name)
        if previous and previous.staging_key == key:
            action, reason, rows = UNCHANGED, "content and rules unchanged; staged rows reused", previous.data_rows
        else:
            if previous is None:
                action, reason = NEW, "new file"
            elif previous.sha256 != sha:
                action, reason = CHANGED, "content changed"
            else:
                action = RESTAGED
                reason = "; ".join(why for changed, why in (
                    (previous.rules_sha256 != rules.sha256, "validation rules changed"),
                    (previous.reference_sha256 != reference.sha256, "reference data changed"),
                    (previous.decisions_sha256 != decisions_sha, "correction decisions changed"),
                ) if changed) or "processing version changed"
            entry = FileEntry(name, str(path), size, sha, rules.sha256, reference.sha256, decisions_sha, key, 0)
            t = time.perf_counter()
            rows = _stage_one(state, run_id, entry, path, rules, checker, suggester, decisions, batch_size)
            event("file staged", file=name, action=action, reason=reason, rows=rows,
                  seconds=round(time.perf_counter() - t, 3))
        actions.append(_action(name, action, reason, sha, size, rows))
        inventory.append({"file": name, "sha256": sha, "bytes": size, "data_rows": rows, "status": "INCLUDED",
                          "duplicate_of": ""})

    current = {p.name for p in inputs}
    for name, entry in registry.items():
        if name not in current:
            state.drop_file(name)
            actions.append(_action(name, REMOVED, "no longer among the inputs; its rows left the results",
                                   entry.sha256, entry.bytes, entry.data_rows))
            event("file removed", file=name, rows=entry.data_rows)
    return actions, inventory


def _stage_one(state: State, run_id: int, entry: FileEntry, path: Path, rules: Rules, checker: RowChecker,
               suggester: cr.Suggester, decisions: dict[str, cr.Decision], batch_size: int) -> int:
    normalizations: Counter = Counter()
    suggestions: list[tuple] = []
    name = entry.name

    def rows():
        for record in read_records(path, rules.encoding):
            row = checker.check(record.values, record.malformed)
            corrected: list[str] = []
            if row.issues and not record.malformed:
                raw = dict(record.values)
                for s in suggester.suggest(name, record.line, record.values, row):
                    decision = decisions.get(s.correction_id)
                    verdict = decision.decision if decision else ""
                    suggestions.append((s.correction_id, name, record.line, row.order_id, s.field, s.rule, s.original,
                                        s.suggested, verdict, decision.reviewer if decision else "",
                                        decision.note if decision else ""))
                    if verdict == cr.APPROVE:
                        raw[s.field] = s.suggested
                        corrected.append(s.field)
                if corrected:
                    row = checker.check(raw)
            normalizations.update(row.normalizations)
            yield (name, record.line, row.order_id, row.customer_id, row.order_date, row.sku, row.quantity,
                   row.amount_cents, row.channel, row.status, "|".join(row.issues),
                   None if row.issues else fingerprint(row, rules.compare_fields), "|".join(corrected),
                   *(record.values[c] for c in COLUMNS), "|".join(record.extra))

    return state.stage_file(entry, run_id, rows(), normalizations, suggestions, batch_size)


def _action(name: str, action: str, reason: str, sha: str, size: int, rows: int) -> dict[str, Any]:
    return {"file": name, "action": action, "reason": reason, "sha256": sha, "bytes": size, "data_rows": rows}
