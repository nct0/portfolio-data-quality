# Roadmap

Milestones match the GitHub milestones of this repository. A milestone is complete only when all of its acceptance criteria have been verified.

**Starting point:** the original case study, kept as the `baseline` tag: three CSV exports with 5,020 rows, a cleaning script and five tests. The baseline numbers are claims to reproduce, not targets.

| Milestone | Status |
|---|---|
| M0 Repository initialized | In progress |
| M1 Development environment reproducible | In progress |
| M2 Core functionality implemented | In progress (Excel report open) |
| M3 Automated validation complete | Not started |
| M4 Case studies complete | Not started |
| M5 Portfolio assets complete | Not started |
| M6 Public release approved | Not started |
| M7 Upwork Catalog published | Not started |

## M0 — Repository initialized

- [x] Original case study imported unchanged and tagged `baseline`
- [ ] Private repository with client-facing README, AGENTS.md, roadmap and release checklist
- [x] Public-safety scan passing in CI

Acceptance: the repository clones on its own, documentation is present, and the scan is clean.

## M1 — Development environment reproducible

- [x] Baseline reproduced and audited: results, defects, limitations (`docs/baseline-audit.md`)
- [x] Enhancements evaluated and the architecture decided before implementation (`docs/feasibility.md`, `docs/architecture.md`, `docs/decisions/`)
- [ ] Python package with pinned, tested dependency versions and a documented virtual-environment setup
- [x] CI workflow that installs the package and runs the tests

Acceptance: a person with Python 3.11+ can set up the project and run the tests by following the README alone.

## M2 — Core functionality implemented

- [x] Deterministic generator: the baseline scenario byte-identical, scale datasets with known expected results, the "day 2" batch
- [x] Streaming reader with header checks, malformed-row handling and explicit encoding
- [x] Validation rules loaded from `config/rules.toml`, with actionable errors for invalid configuration
- [x] Order-independent duplicate resolution with conflict quarantine
- [x] Incremental processing with file identity, content hashes, safe reruns and a full-rebuild option
- [x] Assisted correction: safe normalization, suggestions that need approval, review file and audit trail
- [x] Reconciliation checks and row-level outcomes for every source row
- [x] CSV outputs with formula-injection protection and safe file replacement
- [ ] Reproducible Excel report: dashboard, monthly revenue, data quality, rejected rows, duplicate review, clean orders, processing audit

Acceptance: on the original data the default rules reproduce the baseline business results, or each difference is a documented, tested correction.

## M3 — Automated validation complete

- [ ] Unit and integration tests for every area listed in the brief, including equivalence of full rebuild, incremental runs and batch sizes
- [ ] CI green
- [ ] Benchmarks measured for 5,000, 100,000 and 1,000,000 rows

Acceptance: one command runs all tests, CI is green, and every benchmark figure comes from a committed result.

## M4 — Case studies complete

- [ ] Case study: three messy exports and a late batch to a reconciled report, from the template, every number traced

## M5 — Portfolio assets complete

- [ ] Screenshots of the generated workbook, architecture and data-flow diagrams, before/after illustration
- [ ] README evidence sections filled from measured data
- [ ] Upwork portfolio entry and Catalog text finalized (kept in private notes, outside this repository)

## M6 — Public release approved

- [ ] Every item in `docs/public-release-checklist.md` verified and the owner's approval recorded; the owner changes the visibility

## M7 — Upwork Catalog published

- [ ] The owner has published the portfolio entry and the Catalog offering
