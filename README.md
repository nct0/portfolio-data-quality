# E-commerce Order Data Cleanup & Reconciliation

**Independent portfolio case study | Synthetic data only | No real client involved**

A reproducible data-cleaning assignment modeled after typical freelance requests involving multiple messy CSV exports. The deliverable is an Excel workbook plus audit-friendly CSV exception reports and a small automated test suite.

## Business request

> We have three CSV exports with approximately 5,000 e-commerce orders. Merge them, normalize inconsistent data, flag missing/invalid values, remove exact duplicates, quarantine conflicting duplicates, validate each total against the product catalog, and deliver an Excel overview with monthly completed-order revenue. Do not silently discard problematic rows.

Full client-style requirements and acceptance criteria: [`docs/CLIENT_BRIEF.md`](docs/CLIENT_BRIEF.md).

## Demo results (deterministic synthetic dataset)

| Metric | Count |
|---|---:|
| Source rows | 5,020 |
| Accepted clean orders | 4,905 |
| Rejected rows | 90 |
| Duplicate occurrences | 20 (15 exact; 5 conflicting) |
| Previously accepted originals quarantined due to conflict | 5 |
| Input rows accounted for | **5,020 / 5,020 — PASS** |
| Completed orders used for reporting | 4,170 |
| Gross revenue, Completed status only | **$333,886.85** |

**Reconciliation:** 4,905 accepted + 90 rejected + 20 duplicate occurrences + 5 quarantined originals = 5,020 source rows. We choose to quarantine all versions of any conflicting order ID. Exact duplicates are safely ignored. Revenue excludes `Pending` and `Refunded`, and is gross USD revenue (no fees/taxes or FX).

## Reproduce locally (Python 3.10+; zero external runtime dependencies)

```bash
python src/generate_data.py
python src/clean_orders.py
python -m unittest discover -s tests -v
```

**Windows PowerShell** also supports these commands. Use `py` instead of `python` if required.

The scripts are deterministic (`random.seed(20261108)`), and rerunning them yields identical accepted-row output.

## Files to review

| Location | What it does |
|---|---|
| `src/generate_data.py` | Generate fake orders and intentionally corrupt specific records |
| `data/raw/orders_export_*.csv` | Three incoming sales files, 5,020 rows total |
| `data/reference/products.csv` | Reference prices for validation |
| `src/clean_orders.py` | Normalization, business validation, duplicates, monthly aggregation, quality report |
| `data/processed/clean_orders.csv` | 4,905 clean orders, full data |
| `data/processed/rejected_rows.csv` | Invalid rows + explicit reasons and source file/line |
| `data/processed/duplicates_review.csv` | Exact duplicates and conflicting duplicates |
| `data/processed/monthly_revenue.csv` | Monthly gross completed-order revenue by channel |
| `data/processed/quality_report.json` | Machine-readable reconciliation and issue breakdown |
| `data/processed/Client_Delivery_Workbook.xlsx` | Dashboard, monthly summary, rejected rows, duplicate review, sample of 400 clean orders |
| `tests/test_pipeline.py` | 5 automated tests; checks row accounting, duplicates, totals, reproducibility |

## Data quality rules

- Dates recognized: `YYYY-MM-DD`, `MM/DD/YYYY`, `DD-Mon-YYYY` (other formats explicitly rejected, not guessed).
- SKU, status, and channel are trimmed and case-normalized.
- Quantity must be a positive integer; price must be a valid nonnegative USD amount.
- Order amounts must match `unit_price_usd × quantity` within $0.01.
- Exact duplicate rows count once; **conflicting** duplicate order IDs require review and are excluded entirely.
- Rows failing validation appear in the rejection report, not the clean dataset.
- Only completed orders contribute to the monthly revenue report.
- Source file and line number are retained for exception review.

## Why this is a portfolio project

Many simple data-entry tasks can be completed manually, but this project shows how to combine spreadsheet deliverables with auditable automation, source traceability, business-rule validation, reproducibility and testing. It demonstrates skills relevant to Excel/CSV cleanup, report consolidation and quality checks.

**Disclosure:** All records, customer IDs and prices are fictional. Results describe this demo only, not business outcomes for a real customer.

## Upwork portfolio assets

Suggested 3 images to capture: (1) Dashboard + KPIs, (2) Rejected Rows with reasons, (3) Monthly Summary and PASS check. See [`docs/UPWORK_PORTFOLIO.md`](docs/UPWORK_PORTFOLIO.md).
