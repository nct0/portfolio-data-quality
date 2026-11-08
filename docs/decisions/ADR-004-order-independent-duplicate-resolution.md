# ADR-004: Order-independent duplicate resolution

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The baseline classifies a repeated order ID by comparing it with the first valid version it has seen. After a conflict it forgets that version, so a later exact copy is counted as conflicting. The counts therefore depend on file order: re-ingesting one export under a second file name gave 7 conflicting duplicates instead of 5 ([baseline audit](../baseline-audit.md), defect 5). Incremental runs and different batch splits need results that do not depend on order.

## Decision

1. Duplicates are resolved after all valid rows are staged, by grouping on the normalized order ID and comparing a fingerprint of the compared fields.
2. One distinct version: the occurrence with the smallest (source file, source line) is accepted, and the others are `DUPLICATE_IGNORED` (class `EXACT_DUPLICATE`).
3. Two or more distinct versions: under the default policy `quarantine_all`, every version is `QUARANTINED_CONFLICT` (class `CONFLICTING_DUPLICATE`). This follows the client brief: no arbitrary winner.
4. An optional policy, `keep_first`, accepts the first version in source order and reports the others as `CONFLICT_OVERRIDDEN`. It is never the default, and every overridden row is listed.
5. Rejected rows do not take part in duplicate resolution (baseline behavior, kept).
6. The duplicate review lists every occurrence in a group, with the fields that differ.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep the baseline's streaming comparison | Order-dependent; breaks incremental equivalence |
| "Latest wins" by file date | Needs a trustworthy ordering that exports do not provide; silently replaces data |
| Merge versions field by field | Invents records |

## Consequences

- On the original data, the accepted set and the revenue are unchanged. The report counts rows differently: 15 ignored exact copies and 10 quarantined rows in 5 order IDs, where the baseline reported 20 duplicate occurrences plus 5 quarantined originals. Both add up to 5,020.
- A late conflicting record moves a previously accepted order to quarantine, and the duplicate review shows why.
