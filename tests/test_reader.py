"""Malformed and unusual input files: never a crash, never a silently lost row."""

from __future__ import annotations

import pytest
from conftest import HEADER, order, read_csv, run_pipeline, write_orders

from order_quality.errors import InputError
from order_quality.reader import read_records


def lines_of(path):
    return [(r.line, r.malformed) for r in read_records(path, "utf-8-sig")]


def test_missing_required_column_names_the_column(tmp_path):
    path = write_orders(tmp_path / "in.csv", [order()[:-1]], header=HEADER[:-1])
    with pytest.raises(InputError) as error:
        run_pipeline(tmp_path / "out", [path])
    assert error.value.code == "IN003" and "status" in error.value.message


def test_header_variants_extra_columns_and_column_order_are_accepted(tmp_path):
    header = ["Status", " Order ID ", "customer-id", "ORDER_DATE", "sku", "Quantity", "amount_usd", "channel", "notes"]
    row = order("ORD-7")
    values = dict(zip(HEADER, row))
    path = write_orders(tmp_path / "in.csv", [[values["status"], values["order_id"], values["customer_id"],
                                                values["order_date"], values["sku"], values["quantity"],
                                                values["amount_usd"], values["channel"], "free text"]], header=header)
    result = run_pipeline(tmp_path / "out", [path])
    assert result.report["rows"]["accepted"] == 1
    assert read_csv(tmp_path / "out" / "clean_orders.csv")[0]["order_id"] == "ORD-7"


def test_rows_with_too_many_or_too_few_fields_are_rejected_not_crashing(tmp_path):
    path = write_orders(tmp_path / "in.csv", [order("ORD-1") + ["surplus", "fields"], order("ORD-2")[:5], order("ORD-3")])
    result = run_pipeline(tmp_path / "out", [path])
    assert result.report["rows"] == {"input": 3, "accepted": 1, "rejected": 2, "duplicate_ignored": 0,
                                     "quarantined_conflict": 0, "conflict_overridden": 0}
    rejected = read_csv(tmp_path / "out" / "rejected_rows.csv")
    assert [r["issue_codes"] for r in rejected] == ["MALFORMED_ROW", "MALFORMED_ROW"]
    assert rejected[0]["raw_extra_fields"] == "surplus|fields"
    assert rejected[0]["raw_order_id"] == "ORD-1"


def test_source_line_is_the_physical_line_with_multi_line_fields(tmp_path):
    path = write_orders(tmp_path / "in.csv", [order("ORD-1", customer="C-1\nsecond line"), order("ORD-2", amount="n/a")])
    assert lines_of(path) == [(2, False), (4, False)]
    run_pipeline(tmp_path / "out", [path])
    assert read_csv(tmp_path / "out" / "rejected_rows.csv")[0]["source_line"] == "4"


def test_blank_lines_are_skipped_and_line_numbers_stay_physical(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text(",".join(HEADER) + "\n\n" + ",".join(order("ORD-1")) + "\n\n\n" + ",".join(order("ORD-2")) + "\n",
                    encoding="utf-8")
    assert lines_of(path) == [(3, False), (6, False)]


def test_unterminated_quote_becomes_a_malformed_row(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text(",".join(HEADER) + "\n" + ",".join(order("ORD-1")) + '\n"ORD-2,C-1,2026-01-05\n', encoding="utf-8")
    result = run_pipeline(tmp_path / "out", [path])
    assert result.report["rows"]["accepted"] == 1 and result.report["issues"] == {"MALFORMED_ROW": 1}


def test_oversized_field_becomes_a_malformed_row(tmp_path):
    path = write_orders(tmp_path / "in.csv", [order("ORD-1", customer="X" * 200_000), order("ORD-2")])
    result = run_pipeline(tmp_path / "out", [path])
    assert result.report["rows"]["rejected"] == 1 and result.report["rows"]["accepted"] == 1
    assert result.report["issues"]["MALFORMED_ROW"] == 1


def test_non_utf8_input_fails_with_an_encoding_error(tmp_path):
    path = tmp_path / "in.csv"
    path.write_bytes((",".join(HEADER) + "\n" + ",".join(order(customer="Caf\xe9")) + "\n").encode("latin-1"))
    with pytest.raises(InputError) as error:
        run_pipeline(tmp_path / "out", [path])
    assert error.value.code == "IN004" and "encoding" in error.value.message


def test_empty_file_and_header_only_file(tmp_path):
    empty = tmp_path / "empty" / "a.csv"
    empty.parent.mkdir()
    empty.write_bytes(b"")
    with pytest.raises(InputError) as error:
        run_pipeline(tmp_path / "out1", [empty])
    assert error.value.code == "IN003"
    header_only = write_orders(tmp_path / "header" / "b.csv", [])
    result = run_pipeline(tmp_path / "out2", [header_only])
    assert result.report["rows"]["input"] == 0 and result.exit_code == 0


def test_missing_input_and_empty_folder(tmp_path):
    with pytest.raises(InputError) as error:
        run_pipeline(tmp_path / "out", [tmp_path / "nowhere"])
    assert error.value.code == "IN001"
    (tmp_path / "folder").mkdir()
    with pytest.raises(InputError) as error:
        run_pipeline(tmp_path / "out", [tmp_path / "folder"])
    assert error.value.code == "IN002"


def test_two_inputs_with_the_same_name_are_refused(tmp_path):
    a = write_orders(tmp_path / "a" / "orders.csv", [order("ORD-1")])
    b = write_orders(tmp_path / "b" / "orders.csv", [order("ORD-2")])
    with pytest.raises(InputError) as error:
        run_pipeline(tmp_path / "out", [a, b])
    assert error.value.code == "IN007"


def test_utf8_without_byte_order_mark_is_accepted(tmp_path):
    path = tmp_path / "in.csv"
    path.write_text(",".join(HEADER) + "\n" + ",".join(order("ORD-1")) + "\n", encoding="utf-8")
    assert run_pipeline(tmp_path / "out", [path]).report["rows"]["accepted"] == 1
