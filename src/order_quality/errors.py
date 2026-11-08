"""Stable codes: row issue codes, row outcomes, and errors that stop a run.

Codes are part of the output contract. Never rename or reuse one; add new codes instead.
"""

from __future__ import annotations

# Row issue codes (a rejected row carries one or more, joined with "|").
MISSING_PREFIX = "MISSING_"  # MISSING_<FIELD>, e.g. MISSING_ORDER_ID, MISSING_CUSTOMER_ID
INVALID_DATE = "INVALID_DATE"
UNKNOWN_SKU = "UNKNOWN_SKU"
INVALID_QUANTITY = "INVALID_QUANTITY"
INVALID_AMOUNT = "INVALID_AMOUNT"
UNKNOWN_CHANNEL = "UNKNOWN_CHANNEL"
UNKNOWN_STATUS = "UNKNOWN_STATUS"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
MALFORMED_ROW = "MALFORMED_ROW"

ISSUE_CODES = {
    "MISSING_<FIELD>": "A required field is empty (MISSING_ORDER_ID, MISSING_CUSTOMER_ID, ...).",
    INVALID_DATE: "The date is not in one of the agreed formats.",
    UNKNOWN_SKU: "The SKU is not in the product reference file.",
    INVALID_QUANTITY: "The quantity is not a whole number of at least the configured minimum.",
    INVALID_AMOUNT: "The amount is not a well-formed, non-negative number with at most the allowed decimals.",
    UNKNOWN_CHANNEL: "The sales channel is not in the channel reference file or the approved aliases.",
    UNKNOWN_STATUS: "The order status is not an allowed status or an approved alias.",
    AMOUNT_MISMATCH: "The amount differs from unit price x quantity by more than the tolerance.",
    MALFORMED_ROW: "The record has the wrong number of fields or cannot be parsed as CSV.",
}

# Duplicate classes (duplicates_review.csv).
EXACT_DUPLICATE = "EXACT_DUPLICATE"
CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"

# Row outcomes: every ingested source row gets exactly one.
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
QUARANTINED_CONFLICT = "QUARANTINED_CONFLICT"
CONFLICT_OVERRIDDEN = "CONFLICT_OVERRIDDEN"
OUTCOMES = (ACCEPTED, REJECTED, DUPLICATE_IGNORED, QUARANTINED_CONFLICT, CONFLICT_OVERRIDDEN)

# Errors that stop a run. Each has a code and an exit status.
ERROR_CODES = {
    "CFG001": "Configuration file not found.",
    "CFG002": "Configuration file is not valid TOML.",
    "CFG003": "Unknown section or key in the configuration.",
    "CFG004": "A configuration value has the wrong type.",
    "CFG005": "A configuration value is not allowed.",
    "CFG006": "Missing or unsupported schema_version.",
    "IN001": "An input path does not exist.",
    "IN002": "No input files were found.",
    "IN003": "An input file is missing required columns or has a bad header.",
    "IN004": "An input file is not valid text in the configured encoding.",
    "IN005": "A reference file is missing or invalid.",
    "IN006": "The corrections decision file is missing or invalid.",
    "IN007": "Two input files have the same file name.",
    "ST001": "The state file was written by an incompatible version.",
    "ST002": "The state file is not a readable SQLite database.",
    "OUT001": "An output file cannot be replaced, usually because it is open in another program.",
}


class OrderQualityError(Exception):
    """An error the user can fix. The message says what to change."""

    exit_code = 1

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ConfigError(OrderQualityError):
    exit_code = 2


class InputError(OrderQualityError):
    exit_code = 4


class StateError(OrderQualityError):
    exit_code = 1


class OutputError(OrderQualityError):
    exit_code = 1


EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_CHECK_FAILED = 3
EXIT_INPUT = 4
