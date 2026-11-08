# ADR-003: SQLite staging for scale and processing state

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The baseline holds every source row, output row and fingerprint in memory. The redesign must handle 100,000 rows, and 1,000,000 as a stretch goal, and it must remember what earlier runs processed. Duplicate detection needs all valid rows of an order ID together, which a single streaming pass cannot provide.

## Decision

1. Input files are read row by row. Each row is normalized and validated, then inserted into a SQLite database in batches (`--batch-size`).
2. Duplicate resolution, aggregation and every export are SQL queries with explicit `ORDER BY` keys.
3. Money is stored as integer cents and converted to `Decimal` in Python; floating point is never used for money.
4. The same database file holds the processing state: the input files with their content hashes, the rule and reference versions, and the run history.
5. The database is local working data. By default it lives in the output folder, and it is never committed.

## Alternatives considered

| Option | Why not |
|---|---|
| Python dictionaries | Memory grows with the input, and a separate store would still be needed for incremental runs |
| DuckDB | An extra binary dependency; SQLite is sufficient for this workload |
| External sorted files | More code for the same result |
| Spark or a cloud database | No demonstrated need; cost and operations |

## Consequences

- Memory is bounded by the batch size and SQLite's cache instead of the input size; the benchmarks measure it.
- Disk use grows with the number of rows.
- At 5,000 rows a disk-backed stage is slower than in-memory processing. This is accepted in exchange for one design that serves every size.
