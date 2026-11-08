# ADR-002: Validation rules in a TOML file

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

In the baseline, date formats, statuses, the amount tolerance and the duplicate policy are written inside the cleaning function. Every client has different rules, and some baseline checks are unsafe: quantity is checked with `assert`, which `python -O` removes; `int()` accepts non-ASCII digits; and the amount parser removes every comma and accepts exponent notation ([baseline audit](../baseline-audit.md), defects 1, 2, 6).

## Decision

1. Business rules live in `config/rules.toml`, which carries a `schema_version`. The default file reproduces the baseline rules on the original data.
2. The file is checked before any data is read. Unknown keys, wrong types and invalid values stop the run with a stable `CFG` code, the key path and what to change (for an unknown key, the closest valid name).
3. Checks are explicit code, never `assert`. Quantities accept ASCII digits only; amounts must be well-formed numbers with at most the configured number of decimals; month names are parsed in English whatever the process locale.
4. Reference data (product prices, channels) stays in CSV files, because the client maintains it.

## Alternatives considered

| Option | Why not |
|---|---|
| JSON | No comments in a file meant to be read and edited by people |
| YAML | Third-party package; implicit typing (`NO` becomes `false`) |
| A JSON Schema library | A dependency for a small, fixed schema |
| Rules as Python code | Not reviewable as configuration |

## Consequences

- A rule change is a reviewed edit to one file, covered by a test that shows its effect.
- The stricter number parsing does not change any baseline result: the original data contains none of the affected forms.
