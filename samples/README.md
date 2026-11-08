# Sample outputs

What the pipeline delivers for the day-2 demo run: the three original exports, the late export and the reviewer's demo decisions. Open [`order_quality_report.xlsx`](order_quality_report.xlsx) to see the Excel report without installing anything. All data is synthetic.

Produced by:

```bash
python -m order_quality run --input data/raw --input data/late-batch --corrections corrections/demo-decisions.csv --out output
```

| File | Content |
|---|---|
| `order_quality_report.xlsx` | The Excel report: executive dashboard, monthly revenue, data quality summary, reconciliation audit, rejected records, duplicate review, correction log, clean orders |
| `clean_orders.csv` | 5,157 accepted orders |
| `rejected_rows.csv` | 99 rejected rows, as received, with reason codes |
| `duplicates_review.csv` | Every row of an order ID that occurs more than once |
| `monthly_revenue.csv` | Completed orders and revenue by month and channel |
| `row_outcomes.csv` | One line per source row (5,293) with its outcome |
| `correction_review.csv` | The 17 suggested corrections with the demo decisions filled in (after a first run, the `decision` column is empty for the reviewer) |
| `corrections_applied.csv` | What happened to each suggestion: 14 applied, 1 rejected, 2 pending |
| `quality_report.json` | Counts, revenue, issue statistics, reconciliation checks and the input inventory |

Column definitions: [docs/reference.md](../docs/reference.md#output-files). The files are regenerated with `python scripts/make-samples.py`; `tests/test_samples.py` fails if they no longer match the pipeline's output.
