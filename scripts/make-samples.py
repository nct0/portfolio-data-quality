"""Regenerate samples/: the business outputs and the Excel report of the day-2 demo run.

Usage, from the repository root with the virtual environment active:

    python scripts/make-samples.py

Runs the pipeline on the three original exports plus the late batch, with the demo correction decisions, into a
temporary folder, and copies every business output and the workbook into samples/. tests/test_samples.py fails when
samples/ no longer matches what the pipeline produces.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from order_quality.outputs import BUSINESS_OUTPUTS
from order_quality.pipeline import RunOptions, run
from order_quality.workbook import WORKBOOK

ROOT = Path(__file__).resolve().parents[1]
INPUTS = [ROOT / "data" / "raw", ROOT / "data" / "late-batch"]
SAMPLES = ROOT / "samples"


def main() -> int:
    with tempfile.TemporaryDirectory() as work:
        out = Path(work) / "out"
        result = run(RunOptions(inputs=INPUTS, reference=ROOT / "data" / "reference", out=out,
                                corrections=ROOT / "corrections" / "demo-decisions.csv"))
        if result.exit_code != 0:
            print(f"the demo run failed with exit code {result.exit_code}", file=sys.stderr)
            return 1
        SAMPLES.mkdir(exist_ok=True)
        for name in (*BUSINESS_OUTPUTS, WORKBOOK):
            shutil.copyfile(out / name, SAMPLES / name)
            print(SAMPLES / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
