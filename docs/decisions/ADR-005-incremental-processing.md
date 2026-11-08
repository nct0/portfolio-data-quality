# ADR-005: Incremental runs restage changed files and recompute the global stages

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Exports arrive repeatedly in the same folder; they are sometimes re-sent, and sometimes corrected and sent again. Reprocessing everything every time is wasteful, but an incremental result that drifts from a full rebuild would be worse. Duplicates spread across batches, correction decisions and reference data changes all affect rows that were processed earlier.

## Decision

1. A file's identity is its content hash (SHA-256). Each run classifies every input file as new, changed, unchanged, removed, or a duplicate of another file's content. A duplicate is skipped at file level and listed in the processing audit.
2. Each file's validated rows are stored together with the hash of the file, the rules, the reference data, the normalization code version and the correction decisions that target the file. A file is restaged when any of these differs; each restage is one database transaction.
3. Duplicate resolution, reconciliation and all outputs are recomputed from the stored rows on every run.
4. A change to the rules, the reference data, the normalization version or the state schema restages every file automatically, and the reason is logged. `--full-rebuild` deletes the state first.
5. A removed file's rows leave the results: the outputs always describe the current set of input files.

## Alternatives considered

| Option | Why not |
|---|---|
| Append new rows to the previous results | A late conflict must retract an accepted order from every output; high risk of drift |
| File name and modification time as identity | Copies and touched files are misclassified |
| Always rebuild in full | Correct, but repeats all parsing and validation on every run |

## Consequences

- Results equal a full rebuild by construction, and automated tests also compare full, incremental and different batch sizes byte for byte.
- The global stages still cover all rows, so a run's cost grows with the total size, not only with the new files.
- An interrupted run leaves each file either fully restaged or untouched; the next run continues from there.
