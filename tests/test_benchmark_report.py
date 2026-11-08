"""The benchmark report summarizes every pipeline phase that the measured runs logged."""

from __future__ import annotations

import importlib.util
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / "benchmarks" / "run_benchmark.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("run_benchmark", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_keeps_every_logged_phase_including_the_workbook():
    harness = load_harness()
    phases = {"stage": 3.2, "resolve": 0.9, "export": 1.5, "checks": 0.9, "workbook": 7.7, "total": 14.3}
    runs = [{"scenario": "full", "batch_size": 5000, "repeat": n, "wall_s": 14.5, "exit_code": 0, "peak_rss_mib": 79.0,
             "matches_expected": True, "outputs_identical": True, "pipeline_s": dict(phases)} for n in (1, 2, 3)]
    summary = harness.summarize(runs)
    assert list(summary[0]["pipeline_s"]) == list(phases)
    assert summary[0]["pipeline_s"]["workbook"] == {"median": 7.7, "min": 7.7, "max": 7.7}
