# ADR-007: Deterministic Excel report with XlsxWriter

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The baseline workbook cannot be regenerated from the code, and its dashboard shows twice the true revenue because a formula range includes the total row ([baseline audit](../baseline-audit.md), defect 12). The client-facing report must be correct, reproducible and readable by nontechnical people, and it must stay usable when the data grows beyond what a worksheet should hold.

## Decision

1. The pipeline generates the workbook from its reconciled outputs, with XlsxWriter.
2. Output is deterministic: fixed document properties, fixed sheet order, explicit sort orders. A test regenerates the workbook and compares bytes.
3. Sheets, in this order: Executive Dashboard, Monthly Revenue, Data Quality Summary, Reconciliation Audit, Rejected Records, Duplicate Review, Correction Log, Clean Orders. The dashboard shows the business result on one screen (KPI cards, data-quality indicators, a three-line check summary, charts of the monthly revenue trend, revenue by channel and the distribution of issues); the row accounting, each check's detail, input hashes and rules are on the Reconciliation Audit sheet (revised 2026-11-09, see below).
4. Text from the input is always written as text, never interpreted as a formula. Any formula the report uses is covered by a test.
5. Detail sheets are capped (100,000 rows by default, configurable) with a visible note naming the complete CSV file. The CSV files are always complete.
6. Rows are streamed in XlsxWriter's constant-memory mode. That mode writes each sheet row by row, so merged cells span one row only (dashboard cards are stacked rows) and Excel table objects are not available; detail sheets use filters, frozen and repeated headers and banded rows instead.

## Alternatives considered

| Option | Why not |
|---|---|
| openpyxl for writing | Slower writes and more code for charts; used only to read workbooks in tests |
| pandas with an Excel engine | A heavy dependency for formatting that XlsxWriter does directly |
| CSV only | Not a deliverable for a nontechnical client |
| Every row in the workbook | Hits the 1,048,576-row worksheet limit and becomes slow to open long before that |

## Revision 2026-11-09: executive dashboard

The first dashboard mixed business results with technical evidence (row accounting, six check details, explanations) and needed scrolling. It was redesigned as a one-screen executive view in a navy, teal and neutral palette, and the technical evidence moved to a new Reconciliation Audit sheet, which also took over the Processing Audit content (input files, hashes, rules). Numbers, checks and outputs are unchanged; tests resolve every dashboard chart range against the CSV and JSON outputs and check that the dashboard fits one screen without overlapping charts.

## Consequences

- The workbook can be rebuilt at any time from the same inputs and rules.
- At large sizes the workbook is a summary with previews; the CSV files carry the full data.
