from __future__ import annotations

import datetime
import hashlib
import json
import locale

import pytest
from conftest import DEMO_DECISIONS, LATE_BATCH, RAW, REFERENCE, run_pipeline

from order_quality.generate import generate_baseline, generate_late_batch, generate_scale
from order_quality.normalize import parse_date


def _digest(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def test_baseline_scenario_is_byte_identical_to_the_committed_data(tmp_path):
    generate_baseline(tmp_path)
    for name in ("orders_export_1.csv", "orders_export_2.csv", "orders_export_3.csv"):
        assert (tmp_path / "raw" / name).read_bytes() == (RAW / name).read_bytes(), name
    for name in ("products.csv", "channels.csv"):
        assert (tmp_path / "reference" / name).read_bytes() == (REFERENCE / name).read_bytes(), name


def test_late_batch_and_demo_decisions_are_reproducible(tmp_path):
    generate_late_batch(tmp_path, tmp_path / "corrections")
    for name in ("orders_export_4.csv", "manifest.json"):
        assert (tmp_path / "late-batch" / name).read_bytes() == (LATE_BATCH / name).read_bytes(), name
    assert (tmp_path / "corrections" / "demo-decisions.csv").read_bytes() == DEMO_DECISIONS.read_bytes()


def _german_locale():
    for name in ("de_DE.UTF-8", "de_DE.utf8", "de_DE", "German_Germany.1252", "de-DE"):
        try:
            return locale.setlocale(locale.LC_TIME, name)
        except locale.Error:
            continue
    return None


def test_dates_and_generator_do_not_depend_on_the_process_locale(tmp_path):
    previous = locale.setlocale(locale.LC_TIME)
    if _german_locale() is None:
        pytest.skip("no German locale installed")
    try:
        try:
            datetime.datetime.strptime("16-Dec-2026", "%d-%b-%Y")
            pytest.skip("this platform parses English month names under the German locale")
        except ValueError:
            pass  # the original parser fails here
        assert parse_date("16-Dec-2026", ("%d-%b-%Y",)) == "2026-12-16"
        generate_baseline(tmp_path)
        assert (tmp_path / "raw" / "orders_export_1.csv").read_bytes() == (RAW / "orders_export_1.csv").read_bytes()
    finally:
        locale.setlocale(locale.LC_TIME, previous)


def test_scale_scenario_is_deterministic_per_seed(tmp_path):
    generate_scale(tmp_path / "a", 3000, 3, seed=5)
    generate_scale(tmp_path / "b", 3000, 3, seed=5)
    generate_scale(tmp_path / "c", 3000, 3, seed=6)
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")
    assert _digest(tmp_path / "a") != _digest(tmp_path / "c")


@pytest.mark.parametrize("files", [1, 4])
def test_pipeline_matches_the_scale_manifest_ground_truth(tmp_path, files):
    manifest = generate_scale(tmp_path / "data", 20_000, files, seed=11)
    result = run_pipeline(tmp_path / "out", [tmp_path / "data" / "raw"], reference=tmp_path / "data" / "reference")
    expected = manifest["expected"]
    assert result.exit_code == 0
    assert {k: result.report["rows"][k] for k in expected["rows"]} == expected["rows"]
    assert result.report["revenue"]["amount"] == expected["revenue_usd"]
    assert result.report["revenue"]["orders"] == expected["revenue_orders"]
    assert result.report["issues"] == expected["issues"]
    assert result.report["duplicates"]["conflicting_order_ids"] == expected["conflicting_order_ids"]
    assert result.report["corrections"]["by_rule"] == expected["suggestions"]
    assert expected["rows"]["duplicate_ignored"] > 0 and expected["rows"]["quarantined_conflict"] > 0
    assert json.loads((tmp_path / "data" / "manifest.json").read_text(encoding="utf-8")) == manifest
