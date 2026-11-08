# ADR-001: Python 3.11+, standard library first

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The baseline is two Python scripts with no dependencies. The redesign needs configuration, on-disk staging, hashing, structured logging and an Excel report. Clients and reviewers should be able to install and run it on an ordinary machine in minutes, and every dependency is something to keep up to date.

## Decision

1. Python 3.11 or later, so the standard library's `tomllib` can read the rules file.
2. Standard library for everything except writing Excel: `csv`, `sqlite3`, `hashlib`, `decimal`, `logging`, `tomllib`, `argparse`.
3. One runtime dependency, `XlsxWriter`. Test-only dependencies: `pytest`, and `openpyxl` to read workbooks back. Exact tested versions are pinned in `requirements-dev.txt`.
4. A `src/` package (`order_quality`) with a command-line entry point (`python -m order_quality` or `order-quality`).

## Alternatives considered

| Option | Why not |
|---|---|
| pandas | Large dependency; whole frames in memory; floating-point money by default |
| Keep plain scripts with no package | Hard to test module by module and to install with one command |
| Support Python 3.10 | Would need a third-party TOML reader |

## Consequences

- Installation is a virtual environment plus one `pip install`.
- A new dependency needs its own decision record.
