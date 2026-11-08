# Case 01: Three messy order exports and a late batch to a reconciled Excel report

| | |
|---|---|
| **Repository state** | Tag `baseline` (the original case study, unchanged) → commit `cc46d1f` (the pipeline as measured) |
| **Evidence** | [Baseline audit](../baseline-audit.md) · [tests](../../tests/) · [benchmark results](../../benchmarks/results/README.md) · [day-2 sample outputs](../../samples/) |
| **Status** | Complete |

Independent portfolio case study with synthetic data. The "client" and the requirements in [CLIENT_BRIEF.md](../CLIENT_BRIEF.md) are simulated.

## Problem

A small online retailer sends three CSV order exports, one per sales channel group, 5,020 rows in total, plus a product price list and a list of sales channels. They want one clean order list, monthly revenue of completed orders by channel, and an Excel overview for management. Their operations team must be able to see why any row was left out, and nothing may be deleted or "repaired" silently (requirements R1–R7).

A week later a fourth export arrives: 273 rows that contain new orders, but also orders that were already sent, some of them with a different quantity, and values with spelling mistakes that someone could correct.

The original solution, a 92-line script and a workbook, reproduced the documented numbers. It could not be trusted for the second delivery, or for real client data: its dashboard showed revenue doubled, and its results changed with the order of the files, the Python options and the computer's language setting.

## Reproduction steps

1. **Original behavior.** Extract the tag and run it, as described in the [baseline audit](../baseline-audit.md#2-reproduction):
   ```bash
   git archive baseline | tar -x -C <empty-folder>
   cd <empty-folder>
   python -I src/generate_data.py && python -I src/clean_orders.py
   python -I -O src/clean_orders.py      # the same script with assertions removed
   ```
   The doubled dashboard revenue is in cell `E5` of the committed `data/processed/Client_Delivery_Workbook.xlsx`. The other defects have their own reproduction in the audit's [defect table](../baseline-audit.md#3-defects).
2. **Fixed behavior, day 1.** From a fresh clone, set up the environment as in the [README](../../README.md#5-setup-and-reproduction), then:
   ```bash
   python -m order_quality run --input data/raw --out output
   ```
3. **Day 2.** Into the same output folder, so that the run is incremental:
   ```bash
   python -m order_quality run --input data/raw --input data/late-batch --out output
   python -m order_quality run --input data/raw --input data/late-batch --corrections corrections/demo-decisions.csv --out output
   ```
4. **Scale.** `python benchmarks/run_benchmark.py --dataset 5k --with-baseline-script` (and `100k`, `1m`), as described in [benchmarks/README.md](../../benchmarks/README.md).

## Baseline metrics

The original script and workbook at tag `baseline`:

| Metric | Value | Method |
|---|---|---|
| Documented results | Reproduced: 5,020 rows, 4,905 accepted, 90 rejected, 20 duplicate occurrences, 5 quarantined originals, $333,886.85 | Audit §2, regenerated data and outputs compared byte for byte |
| Dashboard "gross completed revenue" | $667,773.70, twice the true total | Audit §2: the formula's range includes the TOTAL row |
| Workbook reproducible from the code | No: no committed code writes it | Audit §2 |
| Results with `python -O` | 4,915 accepted, 0 `INVALID_QUANTITY`, revenue $334,654.85; the self-check still passes | Audit defect 1 |
| One export ingested twice under a second file name | 1,664 exact and 7 conflicting duplicates (instead of 15 and 5); rejected rows 90 → 113 | Audit defect 5 and §4 |
| Results with a German `LC_TIME` locale | 576 `INVALID_DATE` instead of 10, revenue $293,883.05; the self-check still passes | Audit defect 7 |
| Customer ID `=1+1` | Accepted into `clean_orders.csv` as a formula | Audit defect 8 |
| A row with one extra field | The run stops with `AttributeError` | Audit defect 4 |
| Run time and peak memory, 5,020 rows | 0.19 s (0.18–0.38), 25.5 MiB | [Benchmark](../../benchmarks/results/README.md#5k-5020-rows), 5 runs, median (min–max) |
| Run time and peak memory, 100,469 rows | 1.58 s (1.55–1.84), 161 MiB | [Benchmark](../../benchmarks/results/README.md#100k-100469-rows), 5 runs |

Environment: Windows 11 Enterprise (10.0.26200), Python 3.15.2, 13th Gen Intel Core i7-1370P (20 logical CPUs), 31.6 GiB memory.

## Root-cause analysis

The [audit](../baseline-audit.md#3-defects) traced thirteen defects to five causes:

- **Validation that is not really validation.** Quantity was checked with `assert`, which Python removes under `-O`; `int()` accepts Arabic-Indic digits and underscores; the amount parser removed every comma and rounded extra decimals. Wrong values passed without a trace.
- **Duplicates compared while streaming.** A dictionary kept the first fingerprint of each order ID and replaced it with `None` after a conflict, so a later exact copy was counted as a conflict. The classification depended on which row came first, and the script had no notion of a file it had already seen.
- **Hidden dependencies on the machine.** Month names (`%b`) were parsed and written with the process locale, and output line endings followed the operating system.
- **A check that checks itself.** The accounting check added up the same counters that produced the outputs. It passed in every defect above.
- **A deliverable outside the code.** The workbook was made separately, with a formula whose range included its own total.

## Optimization / fix

The script was replaced by a small package with one job per module (commit `dce384b`; design in [architecture.md](../architecture.md)):

| Cause | Fix | Decision record |
|---|---|---|
| Weak validation | Explicit checks with strict patterns (ASCII digits, well-formed amounts, at most two decimals, English month names on every locale); rules in `config/rules.toml`, checked when loaded | [ADR-002](../decisions/ADR-002-validation-rules-in-toml.md) |
| Order-dependent duplicates | Rows are staged in SQLite; duplicates are resolved over all valid rows of an order ID at once, after every file is staged | [ADR-003](../decisions/ADR-003-sqlite-staging.md), [ADR-004](../decisions/ADR-004-order-independent-duplicate-resolution.md) |
| Re-sent and late files | Files identified by SHA-256; a file with the same content as another is skipped and listed; changed files are restaged; duplicates, totals and outputs are recomputed from all staged rows on every run | [ADR-005](../decisions/ADR-005-incremental-processing.md) |
| Silent repairs | Safe standardization only; anything else becomes a suggestion in a review CSV and is applied only after `APPROVE`; the audit keeps the original, suggested and final value | [ADR-006](../decisions/ADR-006-assisted-correction-review.md) |
| Self-referential check | One outcome per source row in `row_outcomes.csv`; six checks compare independent counts and re-read the written clean and monthly files; a failed check fails the run (exit status 3) | [architecture.md](../architecture.md#failure-handling-and-recovery) |
| Workbook outside the code | Built from the CSV and JSON outputs on every run with XlsxWriter, values instead of formulas, fixed document properties | [ADR-007](../decisions/ADR-007-excel-report.md) (commit `6ee5b73`) |

Rejected alternatives, with reasons, are in [feasibility.md](../feasibility.md): pandas (memory, decimal handling, a large dependency), append-only incremental processing (a late conflict would have to retract published results), applying fuzzy matches automatically (silent changes), and AI-based corrections (non-deterministic, data leaves the machine).

The default rules reproduce the original business result; every change in reporting is listed in [reference.md](../reference.md#corrections-to-the-baseline-rules).

### Day 2: what the late export contains and what the pipeline does with it

The late export ([manifest](../../data/late-batch/manifest.json)) holds 273 rows: 240 new orders, 5 exact re-sends of accepted orders, 2 re-sends with a different quantity and amount, a third version of an order that was already in conflict, 2 copies within the batch itself, 17 values that a reviewer could correct, and 6 values that nobody can safely correct.

| | Day 1 | Day 2, no decisions yet | Day 2, with the reviewer's decisions |
|---|---:|---:|---:|
| Source rows | 5,020 | 5,293 | 5,293 |
| Accepted | 4,905 | 5,143 | 5,157 |
| Rejected | 90 | 113 | 99 |
| Duplicates ignored | 15 | 22 | 22 |
| Quarantined (conflicting versions) | 10 | 15 | 15 |
| Corrections suggested / applied / rejected / pending | 0 | 17 / 0 / 0 / 17 | 17 / 14 / 1 / 2 |
| Completed orders | 4,170 | 4,376 | 4,388 |
| Completed revenue | $333,886.85 | $351,689.90 | $352,317.20 |
| Reconciliation checks | 6 of 6 PASS | 6 of 6 PASS | 6 of 6 PASS |

Sources: day 1 from `tests/test_baseline_regression.py`; day 2 from the manifest's expected effect, asserted by `tests/test_late_batch.py`; the last column is also the committed [`samples/quality_report.json`](../../samples/quality_report.json).

What the client sees:

- The two contradicting re-sends move orders `ORD-000600` and `ORD-000601`, accepted on day 1, into quarantine, with `quantity` and `amount_usd` marked as the differing fields (`test_a_late_conflicting_resend_quarantines_a_previously_accepted_order`).
- The five exact re-sends are counted once; the copies from the new file are the ones ignored (`test_exact_resends_across_batches_are_ignored_once`).
- 14 approved corrections turn rejected rows into accepted orders; the rejected and the two undecided suggestions change nothing (`test_correction_audit_records_original_suggested_and_final_values`, `test_unapproved_suggestions_change_nothing`).
- Only the new file is read; the three day-1 files are recognized by their hash and reused, and the result is byte-identical to a full rebuild (`test_incremental_sequence_equals_full_rebuild`).

## After metrics

| Metric | Before | After | Change |
|---|---|---|---|
| Dashboard revenue | $667,773.70 | $333,886.85, equal to the monthly sheet and the clean detail | Corrected (`test_revenue_is_not_double_counted`) |
| Workbook | Not reproducible | Rebuilt on every run, byte-identical for the same inputs, also for incremental runs and other batch sizes | `test_workbook_bytes_are_deterministic`, `test_same_workbook_for_full_incremental_and_batch_sizes` |
| Zero-quantity rows accepted under `python -O` | 10 | 0 | `test_zero_quantity_is_rejected_under_python_optimize_mode` |
| Duplicate counts when an export is re-sent under another name | 1,664 exact, 7 conflicting | The copy is skipped as duplicate content; results unchanged | `test_same_content_under_another_name_is_not_ingested_twice` |
| Duplicate counts when rows or files are shuffled | Changed with the order (A, A, A′ vs A, A′, A) | Identical business results | `test_classification_is_order_independent`, `test_shuffling_rows_across_files_gives_the_same_business_results` |
| Results under another date locale | 576 rows rejected, $293,883.05 | Identical to the default | `test_dates_and_generator_do_not_depend_on_the_process_locale` |
| Formula-like text in outputs | Written as a formula | Written as text | `test_formula_like_values_are_neutralized_in_every_csv`, `test_formula_like_input_is_stored_as_text` |
| Row with an extra field | Run stops | Row rejected as `MALFORMED_ROW`, run continues | `test_rows_with_too_many_or_too_few_fields_are_rejected_not_crashing` |
| Automated tests | 5 | 202 | `python -m pytest -q` |
| Run time, 5,020 rows | 0.19 s | 0.96 s (0.85–1.09) | 5x slower |
| Run time, 100,469 rows | 1.58 s | 12.4 s (12.2–14.5) | 7.8x slower |
| Peak memory, 100,469 rows | 161 MiB | 78.1 MiB | 52% less |
| Run time and peak memory, 1,004,621 rows | Not measured | 89.5 s (85.9–89.7), 147 MiB | — (3 runs) |

The benchmark figures use the same data, machine and method for both versions: fresh process per run, median of 5 runs (min–max), warm file cache. Every pipeline run matched the expected results, and the incremental, full-rebuild and other-batch-size runs produced business outputs byte-identical to the full run ([results](../../benchmarks/results/README.md)).

## Trade-offs

- **Speed for guarantees.** The pipeline is 5 to 8 times slower than the original script on the same files. It stores every row with its outcome, recomputes duplicates and totals over all rows, re-reads the written files for the checks and builds an 8-sheet workbook, which is its largest phase at 100,469 rows (6.6 of 12.1 s of pipeline time). For a monthly or daily export, seconds are not the constraint; a wrong total is.
- **Incremental runs save less than one might expect.** An unchanged rerun of 100,469 rows takes 8.1 s against 12.4 s for a full run, because only reading and validation are skipped. Recomputing the rest every time is what makes an incremental run provably equal to a full rebuild.
- **Disk instead of memory.** Peak memory stays bounded (147 MiB for 1,004,621 rows), but the state database and outputs take disk space: 51.3 MiB for 100,469 rows, 473 MiB for 1,004,621 rows.
- **Quarantine holds back valid orders.** With the default `quarantine_all` policy, both versions of a conflicting order stay out of revenue until someone decides. A client who prefers the first version can set `keep_first`; the rows are then reported as `CONFLICT_OVERRIDDEN`.
- **More code to maintain.** 3,045 lines in `src/order_quality/` (`wc -l` at `cc46d1f`) against the original 92, offset by 202 tests and a reference that documents every option, rule and code.
- **Excel limits.** Detail sheets stop at 100,000 rows by default, with a note pointing to the complete CSV file.

## Reproducibility notes

- The generator is deterministic: `generate --scenario baseline` reproduces the committed 5,020 rows byte for byte, and each scale dataset's manifest holds its exact expected results.
- Business outputs and the workbook contain no timestamps or paths, so the same inputs give the same bytes on every run and every operating system. Run-specific details go to `processing_audit.csv` and `run_log.jsonl`.
- Benchmarks ran on one laptop with other software open, so times vary between runs; the tables show the range. Peak memory varied by less than 2 MiB between repeats.
- CI runs the tests on Linux with Python 3.11 and 3.13; two tests are skipped there because they need a German locale or Windows file locking. Locally on Windows with Python 3.15.2 all tests run.
