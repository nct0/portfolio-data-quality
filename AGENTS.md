# AGENTS.md — portfolio-data-quality

Instructions for AI agents and humans changing this repository. Read this whole file before changing anything.

## Purpose and boundaries

- A reproducible data-quality pipeline: messy CSV order exports in, clean and reconciled CSV files and an Excel report out, with every source row accounted for.
- This repository is self-contained. It must make sense, run and verify when cloned on its own. Do not reference other repositories, private workspaces or local machine paths in any file.
- It may become public later, so treat every commit, including its history, as publishable.
- The `baseline` tag is the original case study as received. Never move or delete it; the baseline audit depends on it.

## Privacy and data rules

- Synthetic data only, produced by the committed generator. Customer IDs, order IDs and prices are fictional. No names, emails, addresses or phone numbers of real people.
- Never commit secrets, `.env` files, state databases (`*.sqlite`, `*.db`) or run output (`output/`). Committed outputs live only in `samples/` and `tests/fixtures/`.
- Screenshots of the workbook can show window chrome, user names or file paths. Capture the sheet area only and check every image before committing.
- Commit with the repository-local GitHub noreply identity only (`git config user.email` must end in `@users.noreply.github.com`).

## Layout

| Path | Rule |
|---|---|
| `src/order_quality/` | The pipeline package. Business rules stay in pure functions, separate from file and database handling. |
| `config/rules.toml` | Default validation rules. They reproduce the original business rules; change them only with a test that shows the effect. |
| `data/raw/`, `data/reference/` | The original 5,020-row scenario. Regression fixture: byte-identical to what the generator produces. |
| `data/late-batch/`, `corrections/` | The "day 2" export and the demo correction decisions. |
| `tests/` | Unit and integration tests (pytest). `tests/fixtures/baseline/` holds the original outputs. |
| `benchmarks/` | Benchmark harness; `benchmarks/results/` holds committed measurements. |
| `docs/` | Architecture, audit, feasibility, reference, decisions, case study, roadmap, release checklist. |
| `assets/` | Screenshots and diagrams generated from real runs. |

## Python conventions

- Python 3.11 or later. Standard library first; a new dependency needs a decision record in `docs/decisions/`.
- Type hints on public functions. `Decimal` or integer cents for money, never `float`.
- Errors that a user can fix raise the package's error classes with a stable code (`CFG…`, `IN…`, `ST…`, `OUT…`) and a message that says what to change.
- No `assert` for validation (it disappears under `python -O`).
- Outputs are written to a temporary file in the target folder and then moved into place.
- Every output is deterministic: explicit sort orders, no timestamps in business outputs.

## Measurement rules (non-negotiable)

- Never invent, round up or estimate numbers. Every published figure comes from a committed test, run output or benchmark result.
- Benchmarks record dataset size, seed, runtime, peak memory, Python version, OS, CPU and memory. Never record host or user names.
- If a change does not improve something, say so.

## Testing

Before every commit, run from the repository root (with the virtual environment from the README active). Both must pass:

```bash
python -m pytest -q
bash scripts/check-public-safety.sh --history
```

Every bug fix comes with a test that fails without the fix. Tests write only to temporary folders, never into `data/` or `samples/`.

## Documentation

- Keep `README.md` client-facing. Update its numbers only from committed results.
- Update `docs/roadmap.md` when a milestone task is finished, and never mark unfinished work as done.
- Record significant technical decisions in `docs/decisions/`.

## Commits and release

- Small conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), each touching only this repository. No tool-branding trailers.
- Run `bash scripts/check-public-safety.sh --history` before pushing.
- Visibility changes need the owner's explicit, recorded approval in `docs/public-release-checklist.md`. Never change visibility, publish packages or deploy anything yourself.
