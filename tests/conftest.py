from __future__ import annotations

import csv
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from order_quality.outputs import BUSINESS_OUTPUTS
from order_quality.pipeline import RunOptions, RunResult, run

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
REFERENCE = ROOT / "data" / "reference"
LATE_BATCH = ROOT / "data" / "late-batch"
DEMO_DECISIONS = ROOT / "corrections" / "demo-decisions.csv"
FIXTURES = ROOT / "tests" / "fixtures" / "baseline"
RULES_FILE = ROOT / "config" / "rules.toml"
HEADER = ["order_id", "customer_id", "order_date", "sku", "quantity", "amount_usd", "channel", "status"]
PRICES = {"SKU-001": "12.50", "SKU-002": "35.00", "SKU-003": "7.25", "SKU-004": "18.00", "SKU-005": "49.90",
          "SKU-006": "63.00", "SKU-007": "22.00", "SKU-008": "11.00"}
BASELINE_FILES = sorted(RAW.glob("orders_export_*.csv"))


def order(order_id: str = "ORD-1", customer: str = "C-1", date: str = "2026-01-05", sku: str = "SKU-001",
          qty: str = "2", amount: str | None = None, channel: str = "Web", status: str = "Completed") -> list[str]:
    """A valid order row unless a field is overridden with a bad value."""
    if amount is None:
        amount = f"{Decimal(PRICES.get(sku.strip().upper(), '1')) * int(qty):.2f}"
    return [order_id, customer, date, sku, qty, amount, channel, status]


def write_orders(path: Path, rows: list[list[str]], header: list[str] = HEADER) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def run_pipeline(out: Path, inputs: list[Path], **options) -> RunResult:
    """Run the pipeline. The Excel report is skipped unless asked for, to keep the suite fast; test_workbook covers it."""
    options.setdefault("reference", REFERENCE)
    options.setdefault("workbook", False)
    return run(RunOptions(inputs=list(inputs), out=out, **options))


def business_outputs(out: Path) -> dict[str, bytes]:
    return {name: (out / name).read_bytes() for name in BUSINESS_OUTPUTS}


def copy_baseline(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    return [Path(shutil.copy2(f, folder / f.name)) for f in BASELINE_FILES]


@pytest.fixture(scope="session")
def baseline(tmp_path_factory) -> RunResult:
    """One run of the default rules over the original three exports."""
    return run_pipeline(tmp_path_factory.mktemp("baseline") / "out", BASELINE_FILES)
