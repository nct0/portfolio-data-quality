"""Streaming CSV input: one record at a time, with its physical start line."""

from __future__ import annotations

import csv
import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from order_quality.config import COLUMNS
from order_quality.errors import InputError

_HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class Record:
    line: int  # physical line where the record starts (the header is line 1)
    values: dict[str, str]  # business column -> raw text; positional best effort for malformed records
    extra: tuple[str, ...]  # fields beyond the header, or the parse error for unreadable records
    malformed: bool


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def header_key(name: str) -> str:
    """'Order ID', ' order_id ' and 'ORDER-ID' all name the column order_id."""
    return re.sub(r"[\s\-]+", "_", name.strip()).casefold()


def read_records(path: Path, encoding: str) -> Iterator[Record]:
    """Yield every non-blank record. Raises InputError for a bad header or undecodable text."""
    try:
        with path.open(encoding=encoding, newline="") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise InputError("IN003", f"'{path.name}' is empty; expected a header row with {', '.join(COLUMNS)}.") from None
            keys = [header_key(h) for h in header]
            missing = [c for c in COLUMNS if c not in keys]
            if missing:
                raise InputError("IN003", f"'{path.name}' is missing column(s) {', '.join(missing)}. "
                                          f"Expected a header with {', '.join(COLUMNS)} (any order; extra columns are ignored).")
            repeated = sorted({k for k in keys if k in COLUMNS and keys.count(k) > 1})
            if repeated:
                raise InputError("IN003", f"'{path.name}' has column(s) {', '.join(repeated)} more than once.")
            positions = {c: keys.index(c) for c in COLUMNS}
            width = len(header)
            while True:
                start = reader.line_num + 1
                try:
                    fields = next(reader)
                except StopIteration:
                    return
                except csv.Error as exc:
                    yield Record(start, {c: "" for c in COLUMNS}, (f"CSV error: {exc}",), True)
                    continue
                if not fields:
                    continue  # blank line
                if len(fields) != width:
                    values = {c: (fields[i] if i < len(fields) else "") for c, i in positions.items()}
                    yield Record(start, values, tuple(fields[width:]), True)
                    continue
                yield Record(start, {c: fields[i] for c, i in positions.items()}, (), False)
    except UnicodeDecodeError as exc:
        raise InputError("IN004", f"'{path.name}' is not valid {encoding} text (byte {exc.start}: {exc.reason}). "
                                  "Save the file as UTF-8, or set [input] encoding in the rules file.") from exc
