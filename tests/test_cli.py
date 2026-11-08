"""The documented commands, run as a user would run them."""

from __future__ import annotations

import json
import subprocess
import sys

from conftest import RAW, REFERENCE, ROOT, RULES_FILE


def cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "order_quality", *map(str, args)], capture_output=True, text=True,
                          cwd=cwd, encoding="utf-8")


def test_run_on_the_baseline(tmp_path):
    result = cli("run", "--input", RAW, "--reference", REFERENCE, "--config", RULES_FILE, "--out", tmp_path / "out")
    assert result.returncode == 0, result.stderr
    assert "PASS" in result.stdout and "333,886.85" in result.stdout and "4,905" in result.stdout
    assert "file staged" in result.stderr


def test_json_logs_are_one_object_per_line(tmp_path):
    result = cli("run", "--input", RAW, "--reference", REFERENCE, "--out", tmp_path / "out", "--log-format", "json")
    events = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    assert events[0]["event"] == "run started" and events[-1]["event"] == "run finished"
    assert events[-1]["accepted"] == 4905


def test_invalid_config_exits_2(tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text(RULES_FILE.read_text(encoding="utf-8").replace("[amounts]", "[amount]"), encoding="utf-8")
    result = cli("run", "--input", RAW, "--reference", REFERENCE, "--config", rules, "--out", tmp_path / "out")
    assert result.returncode == 2
    assert "error CFG003: rules.toml: unknown section [amount]. Did you mean 'amounts'?" in result.stderr


def test_missing_input_exits_4(tmp_path):
    result = cli("run", "--input", tmp_path / "missing", "--reference", REFERENCE, "--out", tmp_path / "out")
    assert result.returncode == 4 and "error IN001" in result.stderr


def test_generate_baseline(tmp_path):
    result = cli("generate", "--scenario", "baseline", "--out", tmp_path)
    assert result.returncode == 0 and "5,020 rows" in result.stdout
    assert (tmp_path / "raw" / "orders_export_2.csv").read_bytes() == (RAW / "orders_export_2.csv").read_bytes()


def test_generate_scale_and_run_it(tmp_path):
    assert cli("generate", "--scenario", "scale", "--rows", 2000, "--files", 2, "--seed", 4, "--out", tmp_path).returncode == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    result = cli("run", "--input", tmp_path / "raw", "--reference", tmp_path / "reference", "--out", tmp_path / "out",
                 "--quiet", "--batch-size", 100)
    assert result.returncode == 0
    assert not [line for line in result.stderr.splitlines() if line.startswith(("info", "warning"))]
    report = json.loads((tmp_path / "out" / "quality_report.json").read_text(encoding="utf-8"))
    assert report["rows"]["accepted"] == manifest["expected"]["rows"]["accepted"]


def test_bad_batch_size_exits_2(tmp_path):
    result = cli("run", "--input", RAW, "--out", tmp_path / "out", "--batch-size", 0)
    assert result.returncode == 2
