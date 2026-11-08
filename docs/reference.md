# Reference

Commands, rules file, output files, codes and processing behavior of the `order_quality` pipeline. The README explains what the project delivers; this page is the detail behind it.

- [Commands](#commands)
- [Rules file](#rules-file-configrulestoml)
- [Output files](#output-files)
- [Excel report](#excel-report)
- [Row outcomes and row accounting](#row-outcomes-and-row-accounting)
- [Codes](#codes)
- [Incremental processing](#incremental-processing)
- [Correction review](#correction-review)
- [Corrections to the baseline rules](#corrections-to-the-baseline-rules)

## Commands

Run from the repository root with the virtual environment active. `python -m order_quality` and the installed `order-quality` command are the same program.

### `run`

```bash
python -m order_quality run --input data/raw --reference data/reference --config config/rules.toml --out output
```

| Option | Default | Meaning |
|---|---|---|
| `--input PATH` | required | A CSV file or a folder. Repeat it to combine sources (`--input data/raw --input data/late-batch`). Folders are searched, not recursively, for `[input] file_pattern`. File names must be unique across all inputs, because they identify sources in every report. |
| `--reference DIR` | `data/reference` | Folder with `products.csv` (`sku`, `product_name`, `unit_price_usd`) and `channels.csv` (`channel`, `region`). |
| `--out DIR` | `output` | Output folder. Created if missing. |
| `--config FILE` | built-in rules | Rules file. The built-in rules are identical to `config/rules.toml` (a test keeps them equal). |
| `--corrections FILE` | none | Reviewer decisions: an edited `correction_review.csv`. See [Correction review](#correction-review). |
| `--state FILE` | `<out>/state.sqlite` | Processing state for incremental runs. |
| `--full-rebuild` | off | Delete the processing state first and stage every file again. |
| `--batch-size N` | 5000 | Rows per database insert batch. Affects memory and speed only, never results. |
| `--no-workbook` | off | Skip the Excel report. The CSV and JSON outputs are always written. |
| `--log-format text\|json` | `text` | Format of the log on stderr. `json` writes one JSON object per line. |
| `--quiet` | off | Log warnings and errors only. |

The summary (counts, revenue, checks) goes to stdout. The exit status is listed under [Exit codes](#exit-codes).

### `generate`

```bash
python -m order_quality generate --scenario baseline --out data
python -m order_quality generate --scenario late-batch --out data --decisions-dir corrections
python -m order_quality generate --scenario scale --rows 100000 --files 5 --seed 1 --out benchmarks/data/100k
```

| Scenario | Writes | Notes |
|---|---|---|
| `baseline` | `<out>/raw/orders_export_{1,2,3}.csv`, `<out>/reference/*.csv` | The original 5,020-row case study, byte-identical to the committed `data/` files. |
| `late-batch` | `<out>/late-batch/orders_export_4.csv`, `<out>/late-batch/manifest.json`, `<decisions-dir>/demo-decisions.csv` | A "day 2" export of 273 rows and a reviewer's demo decisions. The manifest lists what the batch contains and its exact effect on the baseline results. |
| `scale` | `<out>/raw/orders_export_NN.csv`, `<out>/reference/*.csv`, `<out>/manifest.json` | `--rows` base orders spread over `--files` exports, streamed to disk. Of the base orders, 1.8% get one invalid value, 0.3% a value a reviewer could correct, 0.3% a second, differently formatted copy in another file, and 0.13% a conflicting version in another file (one in ten of those a third version). The manifest holds the exact expected results (row outcomes, issue counts, suggestions, revenue), so a run can be checked against ground truth. |

All generated data is fictional and deterministic: the same arguments always produce the same bytes.

## Rules file (`config/rules.toml`)

TOML, UTF-8. Every key is optional except `schema_version`; a missing key takes the default shown. Unknown sections or keys are errors (with a "did you mean" hint), so a typo can never be silently ignored.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `schema_version` | integer | — (required) | Must be `1`. |
| `[input] encoding` | string | `"utf-8-sig"` | Text encoding of the input files. `utf-8-sig` reads UTF-8 with or without a byte-order mark. Undecodable bytes stop the run (IN004); they are never replaced silently. |
| `[input] file_pattern` | string | `"*.csv"` | File name pattern for `--input` folders. |
| `[fields] required` | list of columns | `["order_id", "customer_id"]` | An empty value is rejected as `MISSING_<FIELD>`. Other checks of that field are skipped. |
| `[fields] uppercase` | list of `order_id`, `customer_id`, `sku` | all three | Values compared and reported in upper case. |
| `[fields] trim_whitespace` | boolean | `true` | Remove leading and trailing spaces. |
| `[dates] formats` | list of formats | `["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"]` | The agreed date formats, tried in order. Directives: `%Y` (four digits), `%m`, `%d`, `%b` (Jan…Dec), `%B` (January…December). Month names are English on every system locale. Each format needs a year, a month and a day. |
| `[amounts] currency` | string | `"USD"` | Three-letter code, used in reports. No conversion is done. |
| `[amounts] tolerance` | quoted decimal | `"0.01"` | Largest allowed difference between the amount and unit price × quantity. Quoted so that the value is exact. |
| `[amounts] max_decimals` | 0, 1 or 2 | `2` | More decimals are rejected (`INVALID_AMOUNT`), never rounded. |
| `[quantity] min` | integer | `1` | Smallest allowed quantity. |
| `[quantity] max` | integer | none | Optional largest quantity. |
| `[statuses] allowed` | list | `["Completed", "Refunded", "Pending"]` | Canonical status names. Matching is case-insensitive. |
| `[statuses] revenue` | list | `["Completed"]` | Statuses that count toward revenue. Must be a subset of `allowed`. |
| `[aliases.channel]` | table | `online`, `web shop` → Web; `in store`, `in-store` → Retail | Approved channel mappings, applied automatically. Keys match case-insensitively with spaces collapsed. Targets must be channels from `channels.csv`. |
| `[aliases.status]` | table | `complete` → Completed | Approved status mappings. Targets must be allowed statuses. |
| `[corrections] sku_format` | boolean | `true` | Suggest `SKU-007` for `SKU007`, `sku 007`, `SKU_007`, … when exactly one known SKU matches. |
| `[corrections] typos` | boolean | `true` | Suggest a status or channel within `max_typo_distance` edits of exactly one value. |
| `[corrections] max_typo_distance` | 1–3 | `2` | Edit distance (Levenshtein) for typo suggestions. |
| `[corrections] min_typo_length` | integer | `4` | Shorter values get no typo suggestion. |
| `[corrections] date_formats` | list of formats | `["%Y/%m/%d", "%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"]` | Unambiguous formats that are not agreed: a match becomes a suggestion, not an accepted date. Must not repeat `[dates] formats`. |
| `[duplicates] conflict_policy` | `"quarantine_all"` or `"keep_first"` | `"quarantine_all"` | What happens when one order ID has several different versions. See [Row outcomes](#row-outcomes-and-row-accounting). |
| `[duplicates] compare_fields` | list of columns | all eight | Fields that make two rows the same version of an order. |
| `[workbook] max_detail_rows` | 1–1,048,575 | `100000` | Most rows per detail sheet in the Excel report. The CSV files always hold every row. |

A configuration mistake stops the run before any file is read, with exit status 2 and a message such as:

```text
error CFG003: rules.toml: unknown key 'tolerence' in [amounts]. Did you mean 'tolerance'?
error CFG004: rules.toml: [amounts] tolerance must be a quoted decimal string such as "0.01" (found 0.01); quotes keep the value exact.
error CFG005: rules.toml: [statuses] revenue lists ['Shipped'], which are not in allowed ['Completed', 'Refunded', 'Pending'].
```

The rules are identified by a SHA-256 hash of their parsed values, so comments and formatting do not count as a change.

## Output files

All files are written to a temporary file in the output folder and then moved into place, so an interrupted run leaves the previous version intact. CSV files are UTF-8 with a byte-order mark (Excel opens them correctly) and use CRLF line endings. Text that a spreadsheet would run as a formula (starting with `=`, `+`, `-`, `@`, tab or carriage return) is written with a leading apostrophe; numbers are written as plain numbers.

| File | One row per | Columns |
|---|---|---|
| `clean_orders.csv` | accepted order, sorted by `order_id` | `order_id`, `customer_id`, `order_date` (ISO), `sku`, `quantity`, `amount_usd`, `channel`, `status`, `source_file`, `source_line` |
| `rejected_rows.csv` | rejected source row, by file and line | `source_file`, `source_line`, `order_id` (normalized), `issue_codes` (joined with `\|`), `raw_<column>` for the eight columns as received, `raw_extra_fields` (fields beyond the header) |
| `duplicates_review.csv` | source row of an order ID that occurs more than once | `order_id`, `classification` (`EXACT_DUPLICATE` / `CONFLICTING_DUPLICATE`), `outcome`, `occurrences`, `versions`, `version` (1 = first version by file and line), `differing_fields`, `source_file`, `source_line`, then the normalized values |
| `monthly_revenue.csv` | channel and month, sorted | `channel`, `month` (YYYY-MM), `completed_orders`, `revenue_usd`: accepted orders with a revenue status |
| `row_outcomes.csv` | every source row, by file and line | `source_file`, `source_line`, `order_id`, `outcome`, `issue_codes`, `corrected_fields` |
| `correction_review.csv` | suggestion | `correction_id`, `source_file`, `source_line`, `order_id`, `field`, `rule`, `original_value`, `suggested_value`, `decision`, `reviewer`, `note` |
| `corrections_applied.csv` | suggestion, then each stale decision | `correction_id`, …, `decision`, `status` (`APPLIED`, `REJECTED`, `PENDING`, `STALE`), `final_value`, `row_outcome` |
| `order_quality_report.xlsx` | — | The Excel report, built from the files above. See [Excel report](#excel-report). |
| `quality_report.json` | run | Row counts, revenue, issue counts, duplicate statistics, normalization counts, correction statistics, reconciliation checks, input inventory (file name, SHA-256, bytes, rows, status), rules and reference hashes. No timestamps and no paths. |
| `processing_audit.csv` | input file in this run | `run_id`, `source_file`, `action`, `reason`, `sha256`, `bytes`, `data_rows` |
| `run_log.jsonl` | run (appended) | Run ID, mode, exit code, seconds per phase, file actions, row counts, revenue |
| `state.sqlite` | — | Processing state (see [Incremental processing](#incremental-processing)). Not a deliverable; never commit it. |

`source_line` is the physical line where the record starts in the file, counting the header as line 1, so it stays correct when a quoted field spans several lines.

The first nine files are **business outputs**: they depend only on the inputs, the rules, the reference data and the decisions. The last three describe the run itself.

## Excel report

`order_quality_report.xlsx` is built at the end of every run (unless `--no-workbook` is given) from the CSV files and `quality_report.json` in the output folder, so it never shows a number those files do not contain. It contains no cell formulas: totals are calculated by the pipeline and written as values, and text from the input is always stored as text.

| Sheet | Content |
|---|---|
| Executive Dashboard | One screen (about 1,180 x 580 pixels at 100% zoom, one landscape page when printed): revenue period; KPI cards for source records, accepted orders, revenue and reconciliation status; indicators for rejected rows, duplicates ignored and quarantined rows; the six checks summarized in three lines; charts of monthly revenue, revenue by sales channel and data-quality issues |
| Monthly Revenue | Revenue and order counts by month and channel with totals, a chart by channel, and the revenue reconciliation check |
| Data Quality Summary | Rejected rows by reason with a short name and an explanation of each code, duplicate statistics and the conflict policy, values standardized automatically, correction suggestions by status and rule |
| Reconciliation Audit | Overall PASS/FAIL, the row accounting (every outcome with its count and share), each reconciliation check with its detail, the input files with row counts and SHA-256, and the rules and reference data |
| Rejected Records | `rejected_rows.csv`: source file and line, reasons and every value as received |
| Duplicate Review | `duplicates_review.csv`; conflicting versions highlighted and the differing values marked |
| Correction Log | `corrections_applied.csv` with the reviewer and note from the decision file; status colored |
| Clean Orders | `clean_orders.csv`, dates as Excel dates and amounts as numbers |

Behavior:

- **Large data.** Detail sheets show at most `[workbook] max_detail_rows` rows (default 100,000). When a CSV has more, the sheet starts with a highlighted note: "Showing the first N of M ... The complete list is in <file>.csv". Rows are streamed to the file, so building the report needs little memory however large the run is.
- **Same inputs, same file.** Sheet order, sort orders and document properties are fixed (the creation date is the latest order date in the clean data), so a full rebuild, an incremental run and any `--batch-size` produce a byte-identical workbook. Per-run file actions are not in the workbook; they are in `processing_audit.csv` and `run_log.jsonl`.
- **Filters.** Detail sheets have a frozen header row with filters, banded rows and print settings (landscape, one page wide, header repeated). They are plain ranges, not Excel tables, because tables cannot be streamed.
- **Charts.** The dashboard charts are native Excel charts that read their values from the Monthly Revenue and Data Quality Summary sheets, and each has alternative text for screen readers. A test resolves every chart range and compares it with the CSV and JSON outputs.
- **Colors.** Navy for structure and revenue, teal for status and channels, neutral greys for text and surfaces. Amber marks data-quality exceptions; red appears only when a check fails.
- **Values starting with `=`, `+`, `-` or `@`** keep the leading apostrophe added in the CSV files, so the sheet shows exactly what the CSV holds and Excel never treats them as formulas.
- **Open in Excel.** If the previous report is open in Excel, the run stops with `OUT001` after the CSV and JSON files are written; the old report file stays unchanged. Close it and run again: unchanged inputs are not restaged, so the rerun is quick.

`scripts/capture-screenshots.ps1` regenerates the images in `assets/` from real runs (Windows with Microsoft Excel).

## Row outcomes and row accounting

Every row of every ingested file gets exactly one outcome:

| Outcome | Meaning |
|---|---|
| `ACCEPTED` | Valid, and the only version of its order ID (or the kept copy of an exact duplicate). In `clean_orders.csv`. |
| `REJECTED` | At least one issue code. In `rejected_rows.csv`. A rejected row never blocks or replaces a valid row with the same order ID. |
| `DUPLICATE_IGNORED` | Valid and identical, after normalization, to an accepted row with the same order ID. |
| `QUARANTINED_CONFLICT` | Valid, but its order ID has two or more different versions. With `quarantine_all`, every version is held back for review. |
| `CONFLICT_OVERRIDDEN` | Only with `keep_first`: a different version of an order ID whose first version (by file name, then line) was accepted. |

Duplicate resolution looks at all staged valid rows at once, so the result does not depend on the order of files, rows or runs. The kept copy of an exact duplicate is the one with the smallest file name and line.

Invariant, checked on every run: **input rows = accepted + rejected + duplicates ignored + quarantined + overridden.** For the original data: 5,020 = 4,905 + 90 + 15 + 10.

A file whose bytes equal an earlier input (by file name order) is skipped as `SKIPPED_DUPLICATE_CONTENT`: it is listed in the input inventory with the file it repeats, and its rows are not counted twice.

### Reconciliation checks

| Check | Passes when |
|---|---|
| `row_accounting` | The row counts recorded for each file, the staged rows and the sum of all outcomes are equal. |
| `file_row_counts` | Every staged file holds as many rows as were read from it. |
| `row_outcomes_complete` | `row_outcomes.csv` has one line per input row. |
| `clean_order_ids_unique` | `clean_orders.csv`, read back from disk, has unique order IDs and as many rows as accepted rows. |
| `clean_amounts_match_prices` | Every clean amount, read back from disk, equals unit price × quantity within the tolerance. |
| `revenue_reconciles` | The totals in `monthly_revenue.csv` equal the revenue-status orders in `clean_orders.csv`, both read back from disk. |

A failed check sets `"status": "FAIL"` in the quality report, logs an error and ends the run with exit status 3. The outputs are kept so that the failure can be investigated.

## Codes

### Row issue codes

| Code | Meaning |
|---|---|
| `MISSING_<FIELD>` | A required field is empty: `MISSING_ORDER_ID`, `MISSING_CUSTOMER_ID`, … |
| `INVALID_DATE` | The date is not in an agreed format (or is not a real date). |
| `UNKNOWN_SKU` | The SKU is not in `products.csv`. |
| `INVALID_QUANTITY` | Not a whole number written with ASCII digits, or outside `[quantity]` min/max. |
| `INVALID_AMOUNT` | Not a well-formed, non-negative number with at most `max_decimals` decimals. Thousands separators are accepted only in groups of three (`1,234.50`). |
| `UNKNOWN_CHANNEL` | Not a channel from `channels.csv` or an approved alias. |
| `UNKNOWN_STATUS` | Not an allowed status or an approved alias. |
| `AMOUNT_MISMATCH` | The amount differs from unit price × quantity by more than the tolerance. |
| `MALFORMED_ROW` | The record has a different number of fields than the header, or cannot be parsed as CSV (for example an oversized field). It is reported alone, because the other values of a broken record cannot be trusted. |

Issue codes appear in the order of this table.

### Errors that stop a run

| Code | Meaning | Exit |
|---|---|---|
| `CFG001` | Rules file not found | 2 |
| `CFG002` | Rules file is not valid TOML | 2 |
| `CFG003` | Unknown section or key | 2 |
| `CFG004` | Value of the wrong type | 2 |
| `CFG005` | Value not allowed | 2 |
| `CFG006` | Missing or unsupported `schema_version` | 2 |
| `IN001` | An input path does not exist | 4 |
| `IN002` | No input files found | 4 |
| `IN003` | An input file is empty, misses required columns or repeats a column | 4 |
| `IN004` | An input file is not valid text in the configured encoding | 4 |
| `IN005` | A reference file is missing or invalid | 4 |
| `IN006` | The decision file is missing or invalid | 4 |
| `IN007` | Two input files have the same name | 4 |
| `ST001` | The state file was written by an incompatible version | 1 |
| `ST002` | The state file is not a readable database | 1 |
| `OUT001` | An output file cannot be replaced (usually open in Excel) | 1 |

Header names are matched case-insensitively, with spaces and hyphens read as underscores (`Order ID` is `order_id`). Extra columns are ignored. Blank lines are skipped.

### Exit codes

| Status | Meaning |
|---|---|
| 0 | Run finished and every reconciliation check passed |
| 1 | Unexpected error, state error or output error |
| 2 | Usage or configuration error |
| 3 | Run finished but a reconciliation check failed |
| 4 | Input error |

## Incremental processing

The state file remembers, for every input file name, the file's SHA-256, its row count and a **staging key**: a hash of the file content, the rules, the reference data, the correction decisions for that file and the pipeline's processing version.

On each run:

| Situation | Action | What happens |
|---|---|---|
| File not seen before | `NEW` | Parsed, validated and staged. |
| Same name, different content | `CHANGED` | The old rows are replaced by the new ones. |
| Same content, but the rules, the reference data or its correction decisions changed | `RESTAGED` | Staged again; the reason is recorded. |
| Same staging key | `UNCHANGED` | Staged rows are reused; the file is only hashed. |
| Content identical to an earlier input | `SKIPPED_DUPLICATE_CONTENT` | Not ingested; listed with the file it repeats. |
| In the state but no longer among the inputs | `REMOVED` | Its rows leave the results. |

Each file is staged in its own database transaction. If a run stops part-way, that file's previous state is kept and the next run stages it again.

After staging, duplicate resolution, all outputs and all checks are recomputed from the complete set of staged rows. That is why an incremental run gives exactly the same business outputs as a full rebuild over the same inputs, whatever the order in which files arrived and whatever the batch size. Automated tests compare the bytes of all business outputs for full rebuilds, incremental sequences, reruns, modified, removed and duplicated files, and batch sizes 1, 7 and 1,000.

**Every file is restaged automatically** when the rules or the reference data change, because every row may be affected. A changed decision file restages only the files it names. A new version of the pipeline that changes normalization or validation results also restages everything (its processing version is part of the staging key).

**A full rebuild (`--full-rebuild`) is needed** only when the state file cannot be used: corrupt (ST002) or from an incompatible version (ST001). The first run into an empty output folder is a full rebuild anyway.

The results always describe the current set of inputs. To keep earlier exports in the results, keep their files in the input folders.

## Correction review

1. Run the pipeline. Rows with a repairable value are rejected as usual, and each suggestion appears in `correction_review.csv` with an empty `decision`.
2. Copy that file, fill `decision` with `APPROVE` or `REJECT` (case-insensitive), and optionally `reviewer` and `note`. Leave rows empty to decide later. Do not change `correction_id`: it identifies the exact file, line, field, original value and suggestion.
3. Run again with `--corrections <your file>`. Approved values replace the original value, the row is validated again, and the change is listed in `corrections_applied.csv` and in the `corrected_fields` column of `row_outcomes.csv`. A row with another issue stays rejected.

A decision applies only while its source row is unchanged. If the file is re-sent with a different value, the ID no longer matches and the decision is reported as `STALE` instead of being applied to different data. The state does not keep decisions between runs: pass the decision file on every run that should use it. Without it, the files it named are restaged without the corrections.

| Rule | Field | Example | Suggestion |
|---|---|---|---|
| `sku_format` | `sku` | `SKU007`, `sku 007`, `SKU.007` | `SKU-007` (only if exactly one known SKU has the same letters and digits) |
| `status_typo` | `status` | `Pendng` | `Pending` (only if exactly one status or alias is nearest within the distance) |
| `channel_typo` | `channel` | `Marketplce` | `Marketplace` |
| `date_format` | `order_date` | `2026/07/14`, `Jul 21, 2026` | `2026-07-14` (only if exactly one date results) |

Order IDs, customer IDs, quantities, amounts and prices are never suggested. Ambiguous values (for example `07/03/26`) get no suggestion and stay rejected for manual review.

## Corrections to the baseline rules

The default rules reproduce the original business results on the original data: 4,905 clean orders with identical values, the same 90 rejected rows with the same issue codes, byte-identical monthly revenue and $333,886.85. The following changes fix defects found in the baseline audit; none of them changes a result on the original data.

| Area | Original behavior | Now | Why |
|---|---|---|---|
| Zero quantity | Checked with `assert`, which `python -O` removes: 10 zero-quantity rows were then accepted and revenue became $334,654.85 | A normal check | Validation must not depend on interpreter flags |
| Quantity digits | `int()` accepted `٣` (Arabic-Indic three) and `1_0` | ASCII digits only | Unexpected digit forms are data errors |
| Amount format | Every comma removed (`1,2,3` became 123); exponents accepted (`1e2`); more than two decimals silently rounded | Thousands separators only in groups of three; no exponents; extra decimals rejected | Financial values are never changed silently |
| Rejected-rows file | `order_id` appeared twice in the header; the normalized ID was overwritten by the raw one | `order_id` (normalized) plus `raw_order_id` and the other `raw_` columns | Machine-readable output needs unique column names |
| Malformed records | A row with extra fields or a file with a missing column crashed the run | `MALFORMED_ROW` for the record; a clear IN003 error for the file | No crash and no silent loss |
| Duplicates | Classification depended on row order: a copy seen after a conflict was counted as conflicting, before it as exact | All valid rows of an order ID are compared at once; with conflicts, every version is quarantined | Results must not depend on file order or batch split |
| Reporting of duplicates | "20 duplicate occurrences + 5 quarantined originals" | 15 ignored exact copies + 10 quarantined rows in 5 order IDs | Same accepted set, clearer categories, one outcome per row |
| Month names | `%b` parsing and generation followed the system locale | English month names on every locale | The same file must give the same result everywhere |
| Line numbers | Counted records, not lines | Physical start line of each record | Traceability with multi-line fields |
| Output files | Overwritten in place; no formula protection | Temporary file + replace; formula guard | Safe reruns and safe opening in Excel |
| Tests | Wrote into the committed output folder | Write to temporary folders only | Running tests must not change the repository |

The original code, data and outputs are kept at the `baseline` tag. The original outputs are also kept in `tests/fixtures/baseline/` for the regression tests.
