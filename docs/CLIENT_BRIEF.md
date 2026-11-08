# Simulated Client Request: Clean Sales Exports

> **Fictional brief created for portfolio training; not a real Upwork contract.**

## Context
We manage a small online retailer selling through Web, Marketplace and Retail channels. Our monthly exports have inconsistent fields, duplicates, questionable order totals, and missing values. Management needs a clean report, and our operations team must be able to audit why any row was excluded.

## Input files
- `orders_export_1.csv`, `orders_export_2.csv`, `orders_export_3.csv` — combined 5,020 rows
- `products.csv` — SKU-to-price lookup
- `channels.csv` — canonical sales channels
- Columns: order_id, customer_id, order_date, sku, quantity, amount_usd, channel, status

## Requirements

**R1. Consolidate:** Load all three files; preserve the ability to trace rejects back to source file and source line.

**R2. Normalize:** Trim spaces; uppercase order/customer IDs and SKUs; normalize casing for channel/status. Parse recognized date formats to ISO dates. Do not guess ambiguous formats beyond the agreed formats.

**R3. Validate:** Reject empty IDs, unparseable dates, nonpositive quantities, invalid money amounts, unknown SKUs/channels/statuses, and total mismatch vs product reference price (tolerance $0.01).

**R4. Duplicate handling:** Ignore exact copies; flag conflicting records sharing an order ID, quarantine all versions of that order ID rather than choosing a random winner.

**R5. Reporting:** Provide monthly gross revenue and completed-order count by sales channel. Pending and Refunded orders should remain in the clean detail but not in gross completed revenue.

**R6. Deliverables:** XLSX workbook; clean CSV; error and duplicate CSV reports; machine-readable quality summary; runnable code; tests.

**R7. Data integrity:** No silent deletion or fabricated repairs. Document assumptions and verify input-row accounting.

## Acceptance criteria
- [x] 5,020 source rows accounted for across accepted, rejected, duplicate, and quarantined original categories.
- [x] 4,905 unique accepted order IDs after conflict quarantine.
- [x] 90 invalid rows with reason codes and traceability.
- [x] 15 exact duplicate occurrences and 5 conflicting duplicate occurrences.
- [x] 5 original records for conflicting IDs also quarantined.
- [x] Monthly completed revenue reconciles to clean detail: $333,886.85.
- [x] Automated tests pass.
- [x] Workbook opens with a dashboard, summary, exceptions and clean-data preview.

## Out of scope
No external API credentials, web scraping, live PII, financial/tax accounting, FX conversions, refunds netting, or real production data. The `Client_Delivery_Workbook.xlsx` intentionally previews 400 clean orders; full dataset is delivered as CSV.
