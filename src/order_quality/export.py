"""Safe output files: formula-injection guard, temporary file plus atomic replace."""

from __future__ import annotations

import csv
import json
import os
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

from order_quality.errors import OutputError

CSV_ENCODING = "utf-8-sig"  # the byte-order mark makes Excel open UTF-8 correctly
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
REPLACE_RETRY_DELAYS = (0.05, 0.1, 0.2, 0.4)  # seconds between attempts; about 0.75 s in total


def guard(value: Any) -> Any:
    """Prefix text that a spreadsheet would run as a formula with an apostrophe. Numbers pass unchanged."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


@contextmanager
def atomic_open(path: Path, newline: str | None = "", encoding: str = CSV_ENCODING) -> Iterator[IO[str]]:
    """Write to a temporary file next to `path`, then replace `path` in one step.

    A failed run never leaves a half-written output: the previous file stays as it was.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline=newline) as handle:
            yield handle
        replace(Path(tmp), path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def replace(tmp: Path, path: Path) -> None:
    """Replace `path` with `tmp`. On Windows a virus scanner or the search indexer can hold a freshly
    written file for a moment, so a refused replace is retried briefly before it counts as locked."""
    for delay in REPLACE_RETRY_DELAYS:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(delay)
    try:
        os.replace(tmp, path)
    except PermissionError as exc:
        raise OutputError("OUT001", f"cannot replace '{path.name}' in '{path.parent}': the file is open in another "
                                    "program (for example Excel) or read-only. Close it and run again.") from exc


def write_csv(path: Path, header: Iterable[str], rows: Iterable[Iterable[Any]]) -> int:
    """Write a CSV with the guard applied to every text cell. Returns the number of data rows."""
    count = 0
    with atomic_open(path) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow([guard(v) for v in row])
            count += 1
    return count


def write_json(path: Path, data: Any) -> None:
    with atomic_open(path, newline="\n", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def append_line(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, ensure_ascii=False) + "\n")
