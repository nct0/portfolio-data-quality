"""samples/ must be exactly what the pipeline produces for the day-2 demo run (regenerate: scripts/make-samples.py)."""

from __future__ import annotations

import zipfile

import pytest
from conftest import BASELINE_FILES, DEMO_DECISIONS, LATE_BATCH, ROOT, run_pipeline

from order_quality.outputs import BUSINESS_OUTPUTS
from order_quality.workbook import WORKBOOK

SAMPLES = ROOT / "samples"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    return run_pipeline(tmp_path_factory.mktemp("samples") / "out", [*BASELINE_FILES, LATE_BATCH / "orders_export_4.csv"],
                        corrections=DEMO_DECISIONS, workbook=True)


@pytest.mark.parametrize("name", BUSINESS_OUTPUTS)
def test_sample_output_is_current(demo, name):
    assert (SAMPLES / name).read_bytes() == (demo.out / name).read_bytes(), \
        f"samples/{name} is out of date; run python scripts/make-samples.py"


def test_sample_workbook_is_current(demo):
    """Compares the unzipped parts, so a different zlib build on another system cannot cause a false failure."""
    with zipfile.ZipFile(SAMPLES / WORKBOOK) as committed, zipfile.ZipFile(demo.out / WORKBOOK) as fresh:
        assert committed.namelist() == fresh.namelist()
        for part in fresh.namelist():
            assert committed.read(part) == fresh.read(part), f"samples/{WORKBOOK}: {part} differs"


def test_samples_hold_only_regenerated_files():
    assert sorted(p.name for p in SAMPLES.iterdir()) == sorted([*BUSINESS_OUTPUTS, WORKBOOK, "README.md"])
