# Feasibility and design decisions

Evaluation of the five planned enhancements, made after the [baseline audit](baseline-audit.md) and before implementation. For each one: feasibility, value to a client, complexity and maintenance cost, scalability, the decision and its trade-offs. Performance figures are **to be measured** by the benchmark harness; none are estimated here.

## Summary

| Enhancement | Priority | Decision | Main reason |
|---|---|---|---|
| A. Large datasets (100,000 and 1,000,000 rows) | P1 / P2 | Implement: streaming reader and SQLite staging | Bounded memory with the standard library only |
| B. Configurable validation rules | P1 | Implement: TOML file, validated at load time | Client rules change without code changes; no dependency |
| C. Incremental processing | P1 | Implement, simplified: restage changed files, always recompute the global stages | Equal to a full rebuild by construction |
| D. Assisted data correction | P1 | Implement: deterministic suggestions approved in a review CSV | Repairs only with an explicit decision and an audit trail |
| E. Professional Excel report | P0 | Implement: XlsxWriter, deterministic bytes, capped detail sheets | The client-facing deliverable; the baseline workbook cannot be regenerated |

Deferred: Excel (`.xlsx`) input, AI-based correction, Spark or cloud processing, an interactive user interface, parallel processing. See [Deferred](#deferred).

## A. Large dataset processing

| | |
|---|---|
| **Feasibility** | High. The CSV module reads row by row; SQLite (in the standard library) holds intermediate data on disk and does grouping and sorting without loading everything into Python. |
| **Client value** | Order, inventory and marketplace exports often exceed what Excel opens comfortably, or arrive as many monthly files. A pipeline that handles them on an ordinary laptop removes manual splitting. |
| **Complexity and maintenance** | Moderate: a small staging schema, batched inserts, ordered queries. Low maintenance: no external services, no extra packages. |
| **Scalability** | Python memory is bounded by the insert batch and SQLite's page cache rather than by the input size. Disk use grows with the number of rows. Expected bottlenecks: per-row normalization and validation in Python, SQLite inserts, sorted CSV export, and the workbook. Runtime and peak memory at 5,000, 100,000 and 1,000,000 rows: to be measured. |

Options considered:

| Option | Assessment |
|---|---|
| Keep everything in Python dictionaries | Simplest, but memory grows with every row, and incremental runs would need a separate store anyway |
| pandas | Familiar, but loads whole frames into memory, adds a large dependency, and makes exact decimal money handling awkward |
| DuckDB | Fast and capable, but an extra binary dependency that this workload does not need |
| External sort files | Bounded memory, but more code than SQLite for the same result |
| **SQLite staging (chosen)** | Standard library, on disk, indexed grouping for duplicates, ordered output, and it doubles as the incremental state store |
| Spark or a cloud service | No demonstrated need; the target sizes fit one machine (see Deferred) |

**Decision: implement now.** Rows are streamed, validated and inserted in batches; duplicate resolution, aggregation and export run as ordered SQL queries. Money is stored as integer cents and converted to `Decimal` at the edges. Determinism comes from explicit sort keys in every query. The batch size is a command-line option and must not change any result (tested). The 1,000,000-row run is a P2 stretch: it is measured if feasible on the reference machine and reported as measured, including if it is slow.

Trade-off: a disk-backed stage is slower than in-memory dictionaries at the 5,000-row scale. It is accepted because the same design serves every size and the incremental state.

## B. Configurable validation rules

| | |
|---|---|
| **Feasibility** | High. `tomllib` reads TOML in Python 3.11 and later. |
| **Client value** | Each client has different statuses, date formats, tolerances and duplicate policies. A readable rules file can be reviewed with the client and changed without touching code. |
| **Complexity and maintenance** | Low to moderate: a hand-written schema check with clear messages. Maintenance: a `schema_version` field, so future changes can be detected. |
| **Scalability** | None. The file is read once per run. |

What moves to configuration: required fields, accepted date formats, amount tolerance and maximum decimals, quantity minimum, allowed and revenue statuses, normalization (trimming, upper-case fields, case-insensitive fields), approved value mappings, which correction suggestions are enabled, duplicate conflict policy and compared fields, and the workbook row cap. What stays in code: what each rule means (for example, how an amount is compared with price × quantity). Product prices and channels stay in the reference CSV files, because they are data that the client maintains.

| Format | Assessment |
|---|---|
| JSON | Standard library, but no comments, which a rules file needs |
| YAML | Comments, but needs a package, and implicit typing turns values such as `NO` into booleans |
| A Python module | Executable code, not reviewable configuration |
| **TOML (chosen)** | Comments, explicit types, standard-library reader |

Invalid configuration fails before any data is read, with a stable code, the file and key path, and what to change, for example an unknown key with the closest valid name. **Decision: implement now.** The default file reproduces the baseline rules; a regression test runs it on the original 5,020 rows.

## C. Incremental processing

| | |
|---|---|
| **Feasibility** | Moderate. Detecting new files is easy; keeping results equal to a full rebuild is the hard part, because of duplicates spread across batches, corrections and reference data changes. |
| **Client value** | Exports arrive daily or monthly into the same folder, are re-sent by mistake, or are corrected and sent again. Safe reruns let a client fix one file and rerun without worrying about double counting. |
| **Complexity and maintenance** | Moderate to high. Maintenance: the state schema carries a version. |
| **Scalability** | Unchanged files are not parsed or validated again. Duplicate resolution, reconciliation and export still cover all rows on every run, so a run cost grows with the total size, not only with the new files. The gain is measured by the benchmarks. |

| Approach | Assessment |
|---|---|
| Append only: process new rows and merge them into the previous results | Fastest, but a late conflicting record must retract an order that was already accepted and reported. That needs retraction logic for every derived output, and drift from a full rebuild is likely |
| **Restage changed files, recompute global stages (chosen)** | Each file's validated rows are stored with the content hash and the rule, reference and correction versions they were built with. Every run then rebuilds duplicates, totals and outputs from the stored rows, so results equal a full rebuild by construction |

File identity is the content hash (SHA-256), not the name or modification time. Per run, each input file is classified as new, changed, unchanged, removed, or a duplicate of another file's content; a file with the same content as one earlier in path order is skipped at file level and listed in the processing audit. Each file is restaged inside one database transaction, so an interrupted run leaves either the old or the new version of that file, never half of it.

Conditions that trigger a full restage of every file (automatic, with the reason logged): the rules file changes, the reference data changes, the normalization code version changes, or the state schema version changes. A manual `--full-rebuild` deletes the state first. A single file is restaged when its content changes or when the correction decisions that target it change. A removed file's rows leave the results, so the results always describe the current set of input files.

**Decision: implement now, in the simplified form.** Automated tests compare a full rebuild, a sequence of incremental runs and different batch sizes, and require byte-identical business outputs.

## D. Assisted data correction

| | |
|---|---|
| **Feasibility** | High for rule-based suggestions. |
| **Client value** | Clients want obvious problems fixed and the rest explained. A review file that opens in Excel, with one decision column, fits how operations teams already work. |
| **Complexity and maintenance** | Moderate. The suggestion rules must stay conservative; each one has tests for what it must not change. |
| **Scalability** | Suggestions are produced while rows are validated; the review file grows with the number of repairable rows, not with the input. |

Three classes:

| Class | Examples | Applied |
|---|---|---|
| 1. Safe deterministic normalization | Surrounding whitespace, letter case, configured approved mappings (for example a channel alias) | Automatically, and counted in the quality report |
| 2. Suggestion that needs approval | SKU format variants that match exactly one known SKU, status or channel typos close to exactly one allowed value, unambiguous alternate date formats | Only after an `APPROVE` decision in the review file; until then the row stays rejected |
| 3. Manual review | Ambiguous dates, values with no unique candidate, any problem in order ID, customer ID, quantity, amount or price | Never; the row stays rejected with its reason |

Financial values, identifiers, prices and conflicting order data are never changed by a rule. Each suggestion has a stable ID derived from the file, line, field, original value and suggested value. A decision applies only while those still match; otherwise it is reported as stale. The audit records the original value, suggested value, rule, decision and final applied value, so a rerun with the same decisions gives the same result.

| Alternative | Assessment |
|---|---|
| Apply fuzzy matches automatically | Silent changes; exactly what the client brief forbids |
| An interactive review screen | More code and a server to run; the CSV review file does the job (deferred) |
| AI-generated suggestions | Non-deterministic, may need an external service and sharing data with it (deferred; could later write suggestions into the same review file, still requiring approval) |

**Decision: implement now with deterministic rules.**

## E. Professional Excel reporting

| | |
|---|---|
| **Feasibility** | High. XlsxWriter writes charts, tables, filters, conditional formats and number formats, and can write row by row to keep memory low. |
| **Client value** | The workbook is what a nontechnical client opens first. It must show the headline numbers, why rows were excluded and what to review, and its totals must be right (the baseline dashboard doubled the revenue). |
| **Complexity and maintenance** | Moderate: layout code for seven sheets. A test checks that the workbook regenerates byte for byte. |
| **Scalability** | A worksheet holds at most 1,048,576 rows, and very large workbooks open slowly. Detail sheets are capped (100,000 rows by default, configurable) with a visible note pointing to the complete CSV file. Workbook time and size at each benchmark size: to be measured. |

Sheets: Executive Dashboard, Monthly Revenue, Data Quality Summary, Rejected Records, Duplicate Review, Clean Orders, Processing Audit, plus the correction log. Charts: monthly revenue trend, revenue by channel, issue distribution. Formatting: frozen headers, filters, currency and integer formats, conditional formatting for failed checks and issue counts. Values that the report shows come from the reconciled outputs; any formula in the workbook is covered by a test, so a range cannot include its own total.

| Option | Assessment |
|---|---|
| openpyxl for writing | Can also read, but writes more slowly and its charts take more code |
| pandas with an Excel engine | A heavy dependency for formatting that XlsxWriter does directly |
| CSV only | Complete, but not a client deliverable |
| **XlsxWriter (chosen)** | Write-only, mature, deterministic output when the document properties are fixed |

openpyxl is used only by the tests, to read the workbook back. **Decision: implement now.** CSV files remain the complete machine-readable output.

## Deferred

| Item | Why deferred | What a client can do today |
|---|---|---|
| Excel (`.xlsx`) input files | A runtime dependency, and typed cells (dates as serial numbers, money as floating point) need their own validation path | Save the sheet as "CSV UTF-8" before running |
| AI-based correction | Non-deterministic, may need an external service and data sharing, hard to test | Rule-based suggestions and manual review |
| Spark, distributed or cloud processing | No demonstrated need: the target sizes are processed on one machine; adds cost and operations | Run locally |
| Interactive user interface | A server and more code; Excel and the review CSV cover the workflow | Open the workbook and the review file in Excel |
| Parallel processing | Only worth it if the benchmarks show a CPU bottleneck that matters; complicates deterministic ordering | Single process |
| Currency conversion, taxes, fees, refund netting | Out of scope in the client brief | Gross USD revenue of completed orders |

## Priorities

| Priority | Scope |
|---|---|
| P0 | Reproduce and audit the baseline; fix data-integrity defects; reproducible, correct workbook; meaningful tests; README, screenshots |
| P1 | Configurable rules, incremental processing, assisted correction, 100,000-row run, reconciliation and audit outputs |
| P2 | 1,000,000-row benchmark, further optimization only where measurements justify it |
