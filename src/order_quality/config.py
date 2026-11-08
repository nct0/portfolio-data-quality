"""Validation rules: loaded from TOML, checked against a schema, frozen for the run.

The built-in defaults are the original business rules. `config/rules.toml` repeats them with comments,
and a test keeps the two identical.
"""

from __future__ import annotations

import codecs
import difflib
import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from order_quality.errors import ConfigError

SCHEMA_VERSION = 1

# The eight business columns of an order export, in the order of the original files.
COLUMNS = ("order_id", "customer_id", "order_date", "sku", "quantity", "amount_usd", "channel", "status")
TEXT_FIELDS = ("order_id", "customer_id", "sku")
CONFLICT_POLICIES = ("quarantine_all", "keep_first")
EXCEL_MAX_DATA_ROWS = 1_048_575  # 1,048,576 rows per worksheet minus the header row

DEFAULTS: dict[str, dict[str, Any]] = {
    "input": {"encoding": "utf-8-sig", "file_pattern": "*.csv"},
    "fields": {
        "required": ["order_id", "customer_id"],
        "uppercase": ["order_id", "customer_id", "sku"],
        "trim_whitespace": True,
    },
    "dates": {"formats": ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"]},
    "amounts": {"currency": "USD", "tolerance": "0.01", "max_decimals": 2},
    "quantity": {"min": 1},
    "statuses": {"allowed": ["Completed", "Refunded", "Pending"], "revenue": ["Completed"]},
    "aliases": {
        "channel": {"online": "Web", "web shop": "Web", "in store": "Retail", "in-store": "Retail"},
        "status": {"complete": "Completed"},
    },
    "corrections": {
        "sku_format": True,
        "typos": True,
        "max_typo_distance": 2,
        "min_typo_length": 4,
        "date_formats": ["%Y/%m/%d", "%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y"],
    },
    "duplicates": {"conflict_policy": "quarantine_all", "compare_fields": list(COLUMNS)},
    "workbook": {"max_detail_rows": 100_000},
}
OPTIONAL_KEYS = {("quantity", "max")}  # keys with no default: absent means "no limit"


@dataclass(frozen=True)
class Rules:
    encoding: str
    file_pattern: str
    required: tuple[str, ...]
    uppercase: tuple[str, ...]
    trim_whitespace: bool
    date_formats: tuple[str, ...]
    currency: str
    tolerance: Decimal
    max_decimals: int
    quantity_min: int
    quantity_max: int | None
    statuses: tuple[str, ...]
    revenue_statuses: tuple[str, ...]
    channel_aliases: dict[str, str] = field(hash=False)
    status_aliases: dict[str, str] = field(hash=False)
    suggest_sku_format: bool
    suggest_typos: bool
    max_typo_distance: int
    min_typo_length: int
    suggest_date_formats: tuple[str, ...]
    conflict_policy: str
    compare_fields: tuple[str, ...]
    workbook_max_detail_rows: int
    source: str
    sha256: str


def default_rules() -> Rules:
    return _build(_merge({}, "built-in defaults", require_version=False), "built-in defaults")


def load_rules(path: Path | None) -> Rules:
    """Load and validate a rules file; `None` means the built-in defaults."""
    if path is None:
        return default_rules()
    if not path.is_file():
        raise ConfigError("CFG001", f"configuration file '{path}' not found. Pass --config with an existing file, "
                                    "or leave it out to use the built-in default rules.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("CFG002", f"'{path.name}' is not valid TOML: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError("CFG002", f"'{path.name}' is not UTF-8 text: {exc}") from exc
    return _build(_merge(data, path.name), path.name)


def _merge(data: dict[str, Any], source: str, require_version: bool = True) -> dict[str, dict[str, Any]]:
    version = data.get("schema_version")
    if require_version and version != SCHEMA_VERSION:
        raise ConfigError("CFG006", f"{source}: schema_version must be {SCHEMA_VERSION} (found {version!r}). "
                                    f"Add 'schema_version = {SCHEMA_VERSION}' at the top of the file.")
    merged = {section: dict(values) for section, values in DEFAULTS.items()}
    for section, values in data.items():
        if section == "schema_version":
            continue
        if section not in DEFAULTS:
            raise ConfigError("CFG003", f"{source}: unknown section [{section}].{_did_you_mean(section, DEFAULTS)}")
        if not isinstance(values, dict):
            raise ConfigError("CFG004", f"{source}: [{section}] must be a table of keys.")
        allowed = set(DEFAULTS[section]) | {k for s, k in OPTIONAL_KEYS if s == section}
        for key, value in values.items():
            if key not in allowed:
                raise ConfigError("CFG003", f"{source}: unknown key '{key}' in [{section}].{_did_you_mean(key, allowed)}")
            merged[section][key] = value
    return merged


def _did_you_mean(name: str, options: Any) -> str:
    close = difflib.get_close_matches(name, sorted(options), n=1)
    return f" Did you mean '{close[0]}'?" if close else f" Allowed: {', '.join(sorted(options))}."


def _build(cfg: dict[str, dict[str, Any]], source: str) -> Rules:
    def get(section: str, key: str, kind: type, what: str) -> Any:
        value = cfg[section].get(key)
        if kind is bool:
            ok = isinstance(value, bool)
        elif kind is int:
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, kind)
        if not ok:
            raise ConfigError("CFG004", f"{source}: [{section}] {key} must be {what} (found {value!r}).")
        return value

    def bad(section: str, key: str, why: str) -> ConfigError:
        return ConfigError("CFG005", f"{source}: [{section}] {key} {why}")

    def text_list(section: str, key: str, allowed: tuple[str, ...] | None = None, empty_ok: bool = False) -> tuple[str, ...]:
        values = get(section, key, list, "a list of strings")
        if not all(isinstance(v, str) and v.strip() for v in values):
            raise ConfigError("CFG004", f"{source}: [{section}] {key} must contain only non-empty strings.")
        if not values and not empty_ok:
            raise bad(section, key, "must not be empty.")
        if len(set(values)) != len(values):
            raise bad(section, key, "lists a value twice.")
        if allowed is not None:
            unknown = [v for v in values if v not in allowed]
            if unknown:
                raise bad(section, key, f"has unknown field(s) {unknown}; allowed: {', '.join(allowed)}.")
        return tuple(values)

    def mapping(section: str, key: str) -> dict[str, str]:
        values = get(section, key, dict, "a table of \"alias\" = \"canonical value\" pairs")
        result: dict[str, str] = {}
        for alias, target in values.items():
            if not isinstance(target, str) or not target.strip() or not alias.strip():
                raise ConfigError("CFG004", f"{source}: [{section}.{key}] \"{alias}\" must map to a non-empty string.")
            k = alias_key(alias)
            if k in result and result[k] != target:
                raise bad(f"{section}.{key}", alias, f"collides with another alias that maps to '{result[k]}'.")
            result[k] = target
        return dict(sorted(result.items()))

    encoding = get("input", "encoding", str, "a text encoding name such as \"utf-8-sig\"")
    try:
        codecs.lookup(encoding)
    except LookupError:
        raise bad("input", "encoding", f"'{encoding}' is not a known text encoding (try \"utf-8-sig\").") from None
    file_pattern = get("input", "file_pattern", str, "a file name pattern such as \"*.csv\"")
    if not file_pattern.strip():
        raise bad("input", "file_pattern", "must not be empty.")

    required = text_list("fields", "required", COLUMNS, empty_ok=True)
    uppercase = text_list("fields", "uppercase", TEXT_FIELDS, empty_ok=True)
    trim = get("fields", "trim_whitespace", bool, "true or false")

    date_formats = text_list("dates", "formats")
    for fmt in date_formats:
        _check_date_format(fmt, source, "dates", "formats")

    currency = get("amounts", "currency", str, "a currency code such as \"USD\"")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise bad("amounts", "currency", f"must be a three-letter code such as \"USD\" (found '{currency}').")
    raw_tolerance = cfg["amounts"].get("tolerance")
    if not isinstance(raw_tolerance, str):
        raise ConfigError("CFG004", f"{source}: [amounts] tolerance must be a quoted decimal string such as \"0.01\" "
                                    f"(found {raw_tolerance!r}); quotes keep the value exact.")
    try:
        tolerance = Decimal(raw_tolerance)
    except InvalidOperation:
        raise bad("amounts", "tolerance", f"'{raw_tolerance}' is not a decimal number.") from None
    if not tolerance.is_finite() or tolerance < 0:
        raise bad("amounts", "tolerance", "must be zero or a positive decimal such as \"0.01\".")
    max_decimals = get("amounts", "max_decimals", int, "a whole number")
    if not 0 <= max_decimals <= 2:
        raise bad("amounts", "max_decimals", f"must be 0, 1 or 2 (found {max_decimals}).")

    quantity_min = get("quantity", "min", int, "a whole number")
    if quantity_min < 0:
        raise bad("quantity", "min", "must be zero or more.")
    quantity_max = cfg["quantity"].get("max")
    if quantity_max is not None:
        quantity_max = get("quantity", "max", int, "a whole number")
        if quantity_max < quantity_min:
            raise bad("quantity", "max", f"({quantity_max}) must not be smaller than min ({quantity_min}).")

    statuses = text_list("statuses", "allowed")
    revenue = text_list("statuses", "revenue", empty_ok=True)
    missing = [s for s in revenue if s not in statuses]
    if missing:
        raise bad("statuses", "revenue", f"lists {missing}, which are not in allowed {list(statuses)}.")

    channel_aliases = mapping("aliases", "channel")
    status_aliases = mapping("aliases", "status")
    wrong = sorted({t for t in status_aliases.values() if t not in statuses})
    if wrong:
        raise bad("aliases", "status", f"maps to {wrong}, which are not in [statuses] allowed {list(statuses)}.")

    sku_format = get("corrections", "sku_format", bool, "true or false")
    typos = get("corrections", "typos", bool, "true or false")
    distance = get("corrections", "max_typo_distance", int, "a whole number")
    if not 1 <= distance <= 3:
        raise bad("corrections", "max_typo_distance", f"must be 1, 2 or 3 (found {distance}).")
    min_length = get("corrections", "min_typo_length", int, "a whole number")
    if min_length < 1:
        raise bad("corrections", "min_typo_length", "must be 1 or more.")
    suggest_dates = text_list("corrections", "date_formats", empty_ok=True)
    for fmt in suggest_dates:
        _check_date_format(fmt, source, "corrections", "date_formats")
    overlap = sorted(set(suggest_dates) & set(date_formats))
    if overlap:
        raise bad("corrections", "date_formats", f"repeats agreed formats {overlap}; list only formats that need approval.")

    policy = get("duplicates", "conflict_policy", str, "a string")
    if policy not in CONFLICT_POLICIES:
        raise bad("duplicates", "conflict_policy", f"'{policy}' is not one of {', '.join(CONFLICT_POLICIES)}.")
    compare = text_list("duplicates", "compare_fields", COLUMNS)

    max_rows = get("workbook", "max_detail_rows", int, "a whole number")
    if not 1 <= max_rows <= EXCEL_MAX_DATA_ROWS:
        raise bad("workbook", "max_detail_rows", f"must be between 1 and {EXCEL_MAX_DATA_ROWS:,} (Excel's row limit).")

    canonical = {section: {k: cfg[section][k] for k in sorted(cfg[section])} for section in sorted(cfg)}
    canonical["aliases"] = {"channel": channel_aliases, "status": status_aliases}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()

    return Rules(
        encoding=encoding, file_pattern=file_pattern, required=required, uppercase=uppercase, trim_whitespace=trim,
        date_formats=date_formats, currency=currency, tolerance=tolerance, max_decimals=max_decimals,
        quantity_min=quantity_min, quantity_max=quantity_max, statuses=statuses, revenue_statuses=revenue,
        channel_aliases=channel_aliases, status_aliases=status_aliases, suggest_sku_format=sku_format,
        suggest_typos=typos, max_typo_distance=distance, min_typo_length=min_length,
        suggest_date_formats=suggest_dates, conflict_policy=policy, compare_fields=compare,
        workbook_max_detail_rows=max_rows, source=source, sha256=digest,
    )


_DIRECTIVE = re.compile(r"%(.)")


def _check_date_format(fmt: str, source: str, section: str, key: str) -> None:
    directives = _DIRECTIVE.findall(fmt)
    unsupported = sorted(set(directives) - set("YmdbB%"))
    if unsupported:
        raise ConfigError("CFG005", f"{source}: [{section}] {key} format '{fmt}' uses %{', %'.join(unsupported)}; "
                                    "supported directives are %Y, %m, %d, %b, %B.")
    has_month = any(d in directives for d in "mbB")
    if "Y" not in directives or "d" not in directives or not has_month:
        raise ConfigError("CFG005", f"{source}: [{section}] {key} format '{fmt}' must contain a four-digit year (%Y), "
                                    "a month (%m, %b or %B) and a day (%d).")


def alias_key(value: str) -> str:
    """Comparison key for aliases and canonical values: case-insensitive, whitespace collapsed."""
    return " ".join(value.split()).casefold()
