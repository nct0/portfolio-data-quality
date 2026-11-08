"""Reference data: the product price list and the canonical sales channels."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from order_quality.config import Rules, alias_key
from order_quality.errors import ConfigError, InputError

PRODUCTS_FILE = "products.csv"
CHANNELS_FILE = "channels.csv"


@dataclass(frozen=True)
class Reference:
    prices: dict[str, Decimal]  # SKU -> unit price
    channels: dict[str, str]  # comparison key -> canonical channel name
    channel_names: tuple[str, ...]
    sha256: str


def load_reference(folder: Path, rules: Rules) -> Reference:
    products_path, channels_path = folder / PRODUCTS_FILE, folder / CHANNELS_FILE
    for path in (products_path, channels_path):
        if not path.is_file():
            raise InputError("IN005", f"reference file '{path.name}' not found in '{folder}'. "
                                      "Pass --reference with the folder that holds products.csv and channels.csv.")

    prices: dict[str, Decimal] = {}
    for line, row in _rows(products_path, ("sku", "unit_price_usd")):
        sku = row["sku"].strip()
        try:
            price = Decimal(row["unit_price_usd"].strip())
        except InvalidOperation:
            price = Decimal(-1)
        if not sku or not price.is_finite() or price < 0:
            raise InputError("IN005", f"{PRODUCTS_FILE} line {line}: needs a SKU and a non-negative unit_price_usd.")
        if sku in prices:
            raise InputError("IN005", f"{PRODUCTS_FILE} line {line}: SKU '{sku}' is listed twice.")
        prices[sku] = price

    channels: dict[str, str] = {}
    for line, row in _rows(channels_path, ("channel",)):
        name = row["channel"].strip()
        if not name:
            raise InputError("IN005", f"{CHANNELS_FILE} line {line}: the channel name is empty.")
        if alias_key(name) in channels:
            raise InputError("IN005", f"{CHANNELS_FILE} line {line}: channel '{name}' is listed twice.")
        channels[alias_key(name)] = name

    if not prices or not channels:
        raise InputError("IN005", f"reference files in '{folder}' must list at least one product and one channel.")
    unknown = sorted({t for t in rules.channel_aliases.values() if t not in channels.values()})
    if unknown:
        raise ConfigError("CFG005", f"{rules.source}: [aliases] channel maps to {unknown}, which are not in "
                                    f"{CHANNELS_FILE} ({', '.join(channels.values())}).")

    digest = hashlib.sha256()
    for path in (products_path, channels_path):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return Reference(prices=prices, channels=channels, channel_names=tuple(channels.values()), sha256=digest.hexdigest())


def _rows(path: Path, required: tuple[str, ...]):
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [c for c in required if c not in (reader.fieldnames or [])]
            if missing:
                raise InputError("IN005", f"{path.name} is missing column(s) {missing}.")
            for line, row in enumerate(reader, start=2):
                if None in row or any(v is None for v in row.values()):
                    raise InputError("IN005", f"{path.name} line {line}: wrong number of fields.")
                yield line, row
    except UnicodeDecodeError as exc:
        raise InputError("IN005", f"{path.name} is not UTF-8 text: {exc}") from exc
