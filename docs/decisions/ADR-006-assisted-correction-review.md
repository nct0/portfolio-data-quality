# ADR-006: Corrections only through an approved review file

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Many rejected rows are obviously repairable, such as a SKU written without its hyphen or a misspelled status. The client brief forbids silent deletion and fabricated repairs, and financial values, identifiers and conflicting order data must never change unless a person decides.

## Decision

1. Three classes. Safe deterministic normalization (whitespace, letter case, configured approved mappings) is applied and counted. A suggestion (a SKU format variant that matches exactly one SKU, a status or channel typo close to exactly one allowed value, an unambiguous alternate date format) needs approval. Everything else stays rejected for manual review.
2. No rule ever changes an order ID, customer ID, quantity, amount or price.
3. Each run writes `correction_review.csv` with one row per suggestion and a decision column. A person sets `APPROVE` or `REJECT` and passes the file back with `--corrections`.
4. Each suggestion has a stable ID derived from the file, line, field, original value and suggested value. A decision applies only while those still match; otherwise it is reported as stale.
5. `corrections_applied.csv` records the original value, suggested value, rule, decision, final value and the row's resulting outcome.

## Alternatives considered

| Option | Why not |
|---|---|
| Apply close matches automatically | Silent changes |
| An interactive review application | A server and more code for what a spreadsheet already does |
| AI-generated suggestions | Non-deterministic and may need an external service; could be added later as another source of suggestions in the same file |

## Consequences

- Reruns with the same decisions give the same results.
- Correction decisions are part of a file's staging key, so changing them restages only the files they target.
