"""Processing state and staging in one SQLite file.

Staged rows keep their raw text, normalized values and issues, so unchanged files are never parsed again.
Each file is staged in its own transaction: a crash leaves the previous state of that file intact.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path

from order_quality.errors import StateError

STATE_SCHEMA_VERSION = "1"
# Part of every staging key. Bump it when a code change alters normalization or validation results,
# so that existing state is restaged automatically.
NORMALIZATION_VERSION = "1"

ROW_COLUMNS = ("file", "line", "order_id", "customer_id", "order_date", "sku", "quantity", "amount_cents", "channel",
               "status", "issues", "fingerprint", "corrected", "raw_order_id", "raw_customer_id", "raw_order_date",
               "raw_sku", "raw_quantity", "raw_amount_usd", "raw_channel", "raw_status", "raw_extra")

_SCHEMA = f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE files (
    name TEXT PRIMARY KEY, path TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
    rules_sha256 TEXT NOT NULL, reference_sha256 TEXT NOT NULL, decisions_sha256 TEXT NOT NULL,
    staging_key TEXT NOT NULL, data_rows INTEGER NOT NULL, staged_run INTEGER NOT NULL);
CREATE TABLE rows (
    file TEXT NOT NULL, line INTEGER NOT NULL, order_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    order_date TEXT NOT NULL, sku TEXT NOT NULL, quantity INTEGER, amount_cents INTEGER, channel TEXT NOT NULL,
    status TEXT NOT NULL, issues TEXT NOT NULL, fingerprint TEXT, corrected TEXT NOT NULL,
    raw_order_id TEXT NOT NULL, raw_customer_id TEXT NOT NULL, raw_order_date TEXT NOT NULL, raw_sku TEXT NOT NULL,
    raw_quantity TEXT NOT NULL, raw_amount_usd TEXT NOT NULL, raw_channel TEXT NOT NULL, raw_status TEXT NOT NULL,
    raw_extra TEXT NOT NULL,
    PRIMARY KEY (file, line)) WITHOUT ROWID;
CREATE INDEX rows_valid_by_order ON rows (order_id, file, line) WHERE fingerprint IS NOT NULL;
CREATE TABLE normalizations (
    file TEXT NOT NULL, field TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL,
    PRIMARY KEY (file, field, kind)) WITHOUT ROWID;
CREATE TABLE suggestions (
    correction_id TEXT PRIMARY KEY, file TEXT NOT NULL, line INTEGER NOT NULL, order_id TEXT NOT NULL,
    field TEXT NOT NULL, rule TEXT NOT NULL, original TEXT NOT NULL, suggested TEXT NOT NULL,
    decision TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE outcomes (file TEXT NOT NULL, line INTEGER NOT NULL, outcome TEXT NOT NULL,
    PRIMARY KEY (file, line)) WITHOUT ROWID;
CREATE TABLE runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, finished_at TEXT, mode TEXT NOT NULL,
    rules_sha256 TEXT NOT NULL, reference_sha256 TEXT NOT NULL, exit_code INTEGER);
INSERT INTO meta VALUES ('schema_version', '{STATE_SCHEMA_VERSION}');
"""


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    bytes: int
    sha256: str
    rules_sha256: str
    reference_sha256: str
    decisions_sha256: str
    staging_key: str
    data_rows: int


class State:
    def __init__(self, connection: sqlite3.Connection, path: Path) -> None:
        self.db = connection
        self.path = path

    @classmethod
    def open(cls, path: Path, full_rebuild: bool = False) -> State:
        if full_rebuild:
            for suffix in ("", "-journal", "-wal", "-shm"):
                Path(f"{path}{suffix}").unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not path.exists() or path.stat().st_size == 0
        db = sqlite3.connect(path, isolation_level=None)
        try:
            db.execute("PRAGMA synchronous = NORMAL")
            db.execute("PRAGMA cache_size = -20000")
            if is_new:
                db.executescript(_SCHEMA)
            row = db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            db.close()
            raise StateError("ST002", f"state file '{path}' is not a readable state database ({exc}). "
                                      "Run again with --full-rebuild to recreate it.") from exc
        if row is None or row[0] != STATE_SCHEMA_VERSION:
            db.close()
            found = row[0] if row else "unknown"
            raise StateError("ST001", f"state file '{path}' uses state schema {found}; this version needs "
                                      f"{STATE_SCHEMA_VERSION}. Run again with --full-rebuild to recreate it.")
        return cls(db, path)

    def close(self) -> None:
        self.db.close()

    # Runs ---------------------------------------------------------------------------------------------------

    def begin_run(self, mode: str, rules_sha256: str, reference_sha256: str) -> int:
        cursor = self.db.execute(
            "INSERT INTO runs (started_at, mode, rules_sha256, reference_sha256) VALUES (?, ?, ?, ?)",
            (_now(), mode, rules_sha256, reference_sha256))
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, exit_code: int) -> None:
        self.db.execute("UPDATE runs SET finished_at = ?, exit_code = ? WHERE run_id = ?", (_now(), exit_code, run_id))

    def previous_run(self, run_id: int) -> dict[str, str] | None:
        row = self.db.execute("SELECT rules_sha256, reference_sha256 FROM runs WHERE run_id < ? "
                              "ORDER BY run_id DESC LIMIT 1", (run_id,)).fetchone()
        return {"rules_sha256": row[0], "reference_sha256": row[1]} if row else None

    # Files and staging --------------------------------------------------------------------------------------

    def files(self) -> dict[str, FileEntry]:
        rows = self.db.execute("SELECT name, path, bytes, sha256, rules_sha256, reference_sha256, decisions_sha256, "
                               "staging_key, data_rows FROM files ORDER BY name")
        return {r[0]: FileEntry(*r) for r in rows}

    def stage_file(self, entry: FileEntry, run_id: int, rows: Iterable[tuple], normalizations: dict[tuple[str, str], int],
                   suggestions: list[tuple], batch_size: int) -> int:
        """Replace everything staged for one file, atomically. `normalizations` and `suggestions` are filled
        while `rows` is consumed. Returns the number of rows staged."""
        insert = f"INSERT INTO rows ({', '.join(ROW_COLUMNS)}) VALUES ({', '.join('?' * len(ROW_COLUMNS))})"
        db = self.db
        db.execute("BEGIN IMMEDIATE")
        try:
            self._delete_file_rows(entry.name)
            staged = 0
            iterator = iter(rows)
            while batch := list(islice(iterator, batch_size)):
                db.executemany(insert, batch)
                staged += len(batch)
            db.executemany("INSERT INTO normalizations VALUES (?, ?, ?, ?)",
                           [(entry.name, f, k, n) for (f, k), n in sorted(normalizations.items())])
            db.executemany("INSERT INTO suggestions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", suggestions)
            db.execute("INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (entry.name, entry.path, entry.bytes, entry.sha256, entry.rules_sha256, entry.reference_sha256,
                        entry.decisions_sha256, entry.staging_key, staged, run_id))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return staged

    def drop_file(self, name: str) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._delete_file_rows(name)
            self.db.execute("DELETE FROM files WHERE name = ?", (name,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _delete_file_rows(self, name: str) -> None:
        for table in ("rows", "normalizations", "suggestions"):
            self.db.execute(f"DELETE FROM {table} WHERE file = ?", (name,))

    # Duplicate resolution -----------------------------------------------------------------------------------

    def resolve(self, conflict_policy: str) -> None:
        """Give every staged row exactly one outcome. Independent of file order and staging history:
        only the set of staged rows and the (file, line) ordering matter."""
        db = self.db
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DROP TABLE IF EXISTS temp.groups")
            db.execute("""CREATE TEMP TABLE groups AS
                SELECT order_id, COUNT(*) AS occurrences, COUNT(DISTINCT fingerprint) AS versions
                FROM rows WHERE fingerprint IS NOT NULL GROUP BY order_id""")
            db.execute("CREATE UNIQUE INDEX temp.groups_order ON groups (order_id)")
            db.execute("DELETE FROM outcomes")
            db.execute("""INSERT INTO outcomes (file, line, outcome)
                SELECT v.file, v.line, CASE
                    WHEN g.versions = 1 THEN CASE WHEN v.pos = 1 THEN 'ACCEPTED' ELSE 'DUPLICATE_IGNORED' END
                    WHEN :policy = 'quarantine_all' THEN 'QUARANTINED_CONFLICT'
                    WHEN v.pos = 1 THEN 'ACCEPTED'
                    WHEN v.fingerprint = v.first_version THEN 'DUPLICATE_IGNORED'
                    ELSE 'CONFLICT_OVERRIDDEN' END
                FROM (SELECT file, line, order_id, fingerprint,
                             ROW_NUMBER() OVER w AS pos, FIRST_VALUE(fingerprint) OVER w AS first_version
                      FROM rows WHERE fingerprint IS NOT NULL
                      WINDOW w AS (PARTITION BY order_id ORDER BY file, line)) AS v
                JOIN groups AS g ON g.order_id = v.order_id""", {"policy": conflict_policy})
            db.execute("INSERT INTO outcomes (file, line, outcome) SELECT file, line, 'REJECTED' FROM rows "
                       "WHERE fingerprint IS NULL")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise

    def query(self, sql: str, params: Iterable | dict = ()) -> Iterator[tuple]:
        return self.db.execute(sql, params)

    def scalar(self, sql: str, params: Iterable | dict = ()) -> int:
        value = self.db.execute(sql, params).fetchone()[0]
        return int(value or 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
