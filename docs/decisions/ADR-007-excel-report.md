# ADR-007: Deterministic Excel report with XlsxWriter

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

The baseline workbook cannot be regenerated from the code, and its dashboard shows twice the true revenue because a formula range includes the total row ([baseline audit](../baseline-audit.md), defect 12). The client-facing report must be correct, reproducible and readable by nontechnical people, and it must stay usable when the data grows beyond what a worksheet should hold.

## Decision

1. The pipeline generates the workbook from its reconciled outputs, with XlsxWriter.
2. Output is deterministic: fixed document properties, fixed sheet order, explicit sort orders. A test regenerates the workbook and compares bytes.
3. Sheets: Executive Dashboard, Monthly Revenue, Data Quality Summary, Rejected Records, Duplicate Review, Clean Orders, Processing Audit, and a correction log. Charts show the monthly revenue trend, revenue by channel and the distribution of issues.
4. Text from the input is always written as text, never interpreted as a formula. Any formula the report uses is covered by a test.
5. Detail sheets are capped (100,000 rows by default, configurable) with a visible note naming the complete CSV file. The CSV files are always complete.

## Alternatives considered

| Option | Why not |
|---|---|
| openpyxl for writing | Slower writes and more code for charts; used only to read workbooks in tests |
| pandas with an Excel engine | A heavy dependency for formatting that XlsxWriter does directly |
| CSV only | Not a deliverable for a nontechnical client |
| Every row in the workbook | Hits the 1,048,576-row worksheet limit and becomes slow to open long before that |

## Consequences

- The workbook can be rebuilt at any time from the same inputs and rules.
- At large sizes the workbook is a summary with previews; the CSV files carry the full data.
