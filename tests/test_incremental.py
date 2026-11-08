"""Incremental runs, reruns and batch sizes must give exactly the business outputs of a full rebuild."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing

import pytest
from conftest import BASELINE_FILES, LATE_BATCH, REFERENCE, RULES_FILE, business_outputs, copy_baseline, run_pipeline

from order_quality import pipeline
from order_quality.errors import StateError

ALL = [*BASELINE_FILES, LATE_BATCH / "orders_export_4.csv"]


def actions(result):
    return {a["file"]: a["action"] for a in result.actions}


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    return run_pipeline(tmp_path_factory.mktemp("full") / "out", ALL)


def test_incremental_sequence_equals_full_rebuild(tmp_path, full):
    out = tmp_path / "out"
    for count in range(1, len(ALL) + 1):
        result = run_pipeline(out, ALL[:count])
        assert actions(result)[ALL[count - 1].name] == "NEW"
        assert all(actions(result)[f.name] == "UNCHANGED" for f in ALL[:count - 1])
    assert business_outputs(out) == business_outputs(full.out)


@pytest.mark.parametrize("batch_size", [1, 7, 1000])
def test_batch_size_never_changes_results(tmp_path, full, batch_size):
    result = run_pipeline(tmp_path / "out", ALL, batch_size=batch_size)
    assert business_outputs(result.out) == business_outputs(full.out)


def test_rerun_without_changes_is_idempotent(tmp_path, full):
    out = tmp_path / "out"
    run_pipeline(out, ALL)
    first = business_outputs(out)
    again = run_pipeline(out, ALL)
    assert set(actions(again).values()) == {"UNCHANGED"}
    assert business_outputs(out) == first == business_outputs(full.out)
    assert again.report["rows"]["input"] == 5293  # nothing ingested twice


def test_modified_file_is_restaged_alone(tmp_path):
    files = copy_baseline(tmp_path / "in")
    out = tmp_path / "out"
    run_pipeline(out, files)
    text = files[1].read_text(encoding="utf-8-sig")
    files[1].write_text(text.replace(text.splitlines()[5], text.splitlines()[5].replace(",", ",,", 1), 1),
                        encoding="utf-8-sig")
    result = run_pipeline(out, files)
    assert actions(result) == {files[0].name: "UNCHANGED", files[1].name: "CHANGED", files[2].name: "UNCHANGED"}
    rebuilt = run_pipeline(tmp_path / "rebuilt", files)
    assert business_outputs(out) == business_outputs(rebuilt.out)
    assert result.report["issues"].get("MALFORMED_ROW") == 1


def test_removed_file_leaves_the_results(tmp_path):
    out = tmp_path / "out"
    run_pipeline(out, BASELINE_FILES)
    result = run_pipeline(out, BASELINE_FILES[:2])
    assert actions(result)[BASELINE_FILES[2].name] == "REMOVED"
    assert business_outputs(out) == business_outputs(run_pipeline(tmp_path / "rebuilt", BASELINE_FILES[:2]).out)


def test_same_content_under_another_name_is_not_ingested_twice(tmp_path, baseline):
    files = copy_baseline(tmp_path / "in")
    shutil.copy2(files[0], tmp_path / "in" / "orders_export_1_copy.csv")
    result = run_pipeline(tmp_path / "out", [tmp_path / "in"])
    assert actions(result)["orders_export_1_copy.csv"] == "SKIPPED_DUPLICATE_CONTENT"
    assert result.report["rows"] == baseline.report["rows"]
    inventory = {i["file"]: i for i in result.report["inputs"]}
    assert inventory["orders_export_1_copy.csv"]["duplicate_of"] == "orders_export_1.csv"


def test_duplicate_content_that_sorts_first_takes_over_consistently(tmp_path):
    files = copy_baseline(tmp_path / "in")
    out = tmp_path / "out"
    run_pipeline(out, files)
    shutil.copy2(files[1], tmp_path / "in" / "a_resent.csv")
    result = run_pipeline(out, [tmp_path / "in"])
    assert actions(result)["a_resent.csv"] == "NEW"
    assert actions(result)["orders_export_2.csv"] == "SKIPPED_DUPLICATE_CONTENT"
    assert business_outputs(out) == business_outputs(run_pipeline(tmp_path / "rebuilt", [tmp_path / "in"]).out)


def test_reference_data_change_restages_everything(tmp_path):
    reference = tmp_path / "reference"
    shutil.copytree(REFERENCE, reference)
    out = tmp_path / "out"
    before = run_pipeline(out, BASELINE_FILES, reference=reference)
    products = reference / "products.csv"
    products.write_text(products.read_text(encoding="utf-8-sig").replace("USB Cable,7.25", "USB Cable,7.50"),
                        encoding="utf-8-sig")
    after = run_pipeline(out, BASELINE_FILES, reference=reference)
    assert set(actions(after).values()) == {"RESTAGED"}
    assert {a["reason"] for a in after.actions} == {"reference data changed"}
    assert after.report["issues"]["AMOUNT_MISMATCH"] > before.report["issues"]["AMOUNT_MISMATCH"]
    rebuilt = run_pipeline(tmp_path / "rebuilt", BASELINE_FILES, reference=reference)
    assert business_outputs(out) == business_outputs(rebuilt.out)


def test_rules_change_restages_everything(tmp_path):
    rules = tmp_path / "rules.toml"
    rules.write_text(RULES_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    out = tmp_path / "out"
    run_pipeline(out, BASELINE_FILES, config=rules)
    rules.write_text(RULES_FILE.read_text(encoding="utf-8").replace('tolerance = "0.01"', 'tolerance = "0"'),
                     encoding="utf-8")
    after = run_pipeline(out, BASELINE_FILES, config=rules)
    assert {a["reason"] for a in after.actions} == {"validation rules changed"}
    rebuilt = run_pipeline(tmp_path / "rebuilt", BASELINE_FILES, config=rules)
    assert business_outputs(out) == business_outputs(rebuilt.out)


def test_crash_while_staging_a_file_is_recovered_by_the_next_run(tmp_path, monkeypatch, full):
    out = tmp_path / "out"
    original = pipeline.read_records

    def failing(path, encoding):
        for i, record in enumerate(original(path, encoding)):
            if path.name == "orders_export_2.csv" and i == 900:
                raise RuntimeError("simulated crash")
            yield record

    monkeypatch.setattr(pipeline, "read_records", failing)
    with pytest.raises(RuntimeError):
        run_pipeline(out, ALL)
    with closing(sqlite3.connect(out / "state.sqlite")) as db:
        staged = dict(db.execute("SELECT name, data_rows FROM files"))
        rows_of_file_2 = db.execute("SELECT COUNT(*) FROM rows WHERE file = 'orders_export_2.csv'").fetchone()[0]
    assert staged == {"orders_export_1.csv": 1674} and rows_of_file_2 == 0  # the failed file left no partial rows
    monkeypatch.setattr(pipeline, "read_records", original)
    result = run_pipeline(out, ALL)
    assert actions(result)["orders_export_1.csv"] == "UNCHANGED"
    assert business_outputs(out) == business_outputs(full.out)


def test_incompatible_or_corrupt_state_needs_a_full_rebuild(tmp_path, full):
    out = tmp_path / "out"
    run_pipeline(out, ALL)
    with closing(sqlite3.connect(out / "state.sqlite")) as db:
        db.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        db.commit()
    with pytest.raises(StateError) as error:
        run_pipeline(out, ALL)
    assert error.value.code == "ST001" and "--full-rebuild" in error.value.message
    (out / "state.sqlite").write_bytes(b"not a database at all, just text" * 10)
    with pytest.raises(StateError) as error:
        run_pipeline(out, ALL)
    assert error.value.code == "ST002"
    result = run_pipeline(out, ALL, full_rebuild=True)
    assert set(actions(result).values()) == {"NEW"}
    assert business_outputs(out) == business_outputs(full.out)


def test_state_can_live_outside_the_output_folder(tmp_path, full):
    result = run_pipeline(tmp_path / "out", ALL, state=tmp_path / "state" / "orders.sqlite")
    assert (tmp_path / "state" / "orders.sqlite").is_file() and not (tmp_path / "out" / "state.sqlite").exists()
    assert business_outputs(result.out) == business_outputs(full.out)
