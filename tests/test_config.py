from __future__ import annotations

import pytest
from conftest import BASELINE_FILES, REFERENCE, RULES_FILE, order, run_pipeline, write_orders

from order_quality.config import default_rules, load_rules
from order_quality.errors import ConfigError
from order_quality.reference import load_reference

RULES = RULES_FILE.read_text(encoding="utf-8")


def write_rules(tmp_path, text: str):
    path = tmp_path / "rules.toml"
    path.write_text(text, encoding="utf-8")
    return path


def edited(old: str, new: str) -> str:
    assert old in RULES, old
    return RULES.replace(old, new, 1)


@pytest.mark.parametrize("text, code, hint", [
    (edited("[amounts]", "[amount]"), "CFG003", "Did you mean 'amounts'"),
    (edited('tolerance = "0.01"', 'tolerence = "0.01"'), "CFG003", "Did you mean 'tolerance'"),
    (edited('tolerance = "0.01"', "tolerance = 0.01"), "CFG004", "quoted decimal"),
    (edited('tolerance = "0.01"', 'tolerance = "-1"'), "CFG005", "tolerance"),
    (edited('tolerance = "0.01"', 'tolerance = "abc"'), "CFG005", "not a decimal"),
    (edited("max_decimals = 2", "max_decimals = 3"), "CFG005", "0, 1 or 2"),
    (edited('revenue = ["Completed"]', 'revenue = ["Shipped"]'), "CFG005", "not in allowed"),
    (edited('allowed = ["Completed", "Refunded", "Pending"]', "allowed = []"), "CFG005", "must not be empty"),
    (edited('formats = ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"]', 'formats = ["%Y-%q-%d"]'), "CFG005", "%q"),
    (edited('formats = ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"]', 'formats = ["%Y-%m"]'), "CFG005", "day (%d)"),
    (edited('conflict_policy = "quarantine_all"', 'conflict_policy = "newest"'), "CFG005", "quarantine_all, keep_first"),
    (edited('"complete" = "Completed"', '"complete" = "Done"'), "CFG005", "not in [statuses]"),
    (edited('encoding = "utf-8-sig"', 'encoding = "utf-99"'), "CFG005", "not a known text encoding"),
    (edited('required = ["order_id", "customer_id"]', 'required = ["order_id", "email"]'), "CFG005", "email"),
    (edited("max_detail_rows = 100000", "max_detail_rows = 2000000"), "CFG005", "Excel's row limit"),
    (edited("min = 1", "min = 5\nmax = 2"), "CFG005", "must not be smaller than min"),
    (edited("trim_whitespace = true", 'trim_whitespace = "yes"'), "CFG004", "true or false"),
    (edited("schema_version = 1", "schema_version = 2"), "CFG006", "schema_version must be 1"),
    (edited("schema_version = 1", ""), "CFG006", "schema_version"),
    (edited("[workbook]", "[workbook"), "CFG002", "not valid TOML"),
])
def test_invalid_configuration_fails_with_an_actionable_message(tmp_path, text, code, hint):
    with pytest.raises(ConfigError) as error:
        load_rules(write_rules(tmp_path, text))
    assert error.value.code == code
    assert hint in error.value.message
    assert "rules.toml" in error.value.message


def test_missing_configuration_file(tmp_path):
    with pytest.raises(ConfigError) as error:
        load_rules(tmp_path / "nope.toml")
    assert error.value.code == "CFG001"


def test_channel_alias_must_name_a_reference_channel(tmp_path):
    rules = load_rules(write_rules(tmp_path, edited('"online" = "Web"', '"online" = "Website"')))
    with pytest.raises(ConfigError) as error:
        load_reference(REFERENCE, rules)
    assert error.value.code == "CFG005" and "Website" in error.value.message


def test_built_in_defaults_are_the_original_rules():
    rules = default_rules()
    assert rules.date_formats == ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y")
    assert str(rules.tolerance) == "0.01" and rules.quantity_min == 1
    assert rules.statuses == ("Completed", "Refunded", "Pending") and rules.revenue_statuses == ("Completed",)
    assert rules.required == ("order_id", "customer_id") and rules.conflict_policy == "quarantine_all"


def test_comments_and_formatting_do_not_change_the_rules_hash(tmp_path):
    text = "\n".join(line.split("#")[0].rstrip() if "#" in line and '"#' not in line else line
                     for line in RULES.splitlines())
    assert load_rules(write_rules(tmp_path, text)).sha256 == load_rules(RULES_FILE).sha256


def test_configuration_changes_business_rules(tmp_path):
    inputs = [write_orders(tmp_path / "in.csv", [order("ORD-1", status="Refunded"), order("ORD-2")])]
    default = run_pipeline(tmp_path / "a", inputs)
    assert default.report["revenue"]["orders"] == 1
    wider = write_rules(tmp_path, edited('revenue = ["Completed"]', 'revenue = ["Completed", "Refunded"]'))
    result = run_pipeline(tmp_path / "b", inputs, config=wider)
    assert result.report["revenue"]["orders"] == 2


def test_required_fields_are_configurable(tmp_path):
    rules = write_rules(tmp_path, edited('required = ["order_id", "customer_id"]', 'required = ["order_id"]'))
    inputs = [write_orders(tmp_path / "in.csv", [order("ORD-1", customer="")])]
    assert run_pipeline(tmp_path / "a", inputs).report["rows"]["rejected"] == 1
    assert run_pipeline(tmp_path / "b", inputs, config=rules).report["rows"]["accepted"] == 1


def test_default_rules_file_runs_the_baseline(tmp_path):
    result = run_pipeline(tmp_path / "out", BASELINE_FILES, config=RULES_FILE)
    assert result.report["rows"]["accepted"] == 4905
