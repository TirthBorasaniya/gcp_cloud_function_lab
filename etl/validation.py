"""
Parses uploaded file content and validates every record against a table schema.
Values are converted to the JSON representation BigQuery expects, so CSV text becomes typed values.
"""

import csv
import io
import json
import math
import re
from datetime import date, datetime

from etl.errors import FileValidationError

INTEGER_PATTERN = re.compile(r"[+-]?\d+")
TRUE_STRINGS = ("true", "1", "yes")
FALSE_STRINGS = ("false", "0", "no")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_records(content: bytes, file_format: str) -> list:
    """
    Parse raw file content into a list of records.

    Parameters
    ----------
    content : bytes
        Raw object content downloaded from Cloud Storage.
    file_format : str
        "NEWLINE_DELIMITED_JSON" or "CSV".

    Returns
    -------
    record_list : list of dict
        One dict per JSON line or CSV row. Empty CSV cells become None.
    """
    try:
        # utf-8-sig strips the byte order mark that spreadsheet exports often add
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as err:
        raise FileValidationError([f"file is not valid UTF-8: {err}"]) from err

    record_list = _parse_csv(text) if file_format == "CSV" else _parse_ndjson(text)
    if not record_list:
        raise FileValidationError(["file contains no records"])
    return record_list


def _parse_ndjson(text: str) -> list:
    """Parse newline-delimited JSON, skipping blank lines."""
    record_list = []
    error_list = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as err:
            error_list.append(f"line {line_number}: invalid JSON ({err.msg})")
            continue
        if not isinstance(record, dict):
            error_list.append(f"line {line_number}: expected a JSON object")
            continue
        record_list.append(record)

    if error_list:
        raise FileValidationError(error_list)
    return record_list


def _parse_csv(text: str) -> list:
    """Parse CSV with a header row; empty cells become None."""
    reader = csv.DictReader(io.StringIO(text))
    record_list = []
    error_list = []
    try:
        if not reader.fieldnames:
            return []
        for row in reader:
            # DictReader stores surplus cells under the key None
            if None in row:
                error_list.append(f"line {reader.line_num}: more values than header columns")
                continue
            record_list.append({key: (value if value else None) for key, value in row.items()})
    except csv.Error as err:
        raise FileValidationError([f"line {reader.line_num}: malformed CSV ({err})"]) from err

    if error_list:
        raise FileValidationError(error_list)
    return record_list


# ---------------------------------------------------------------------------
# Type conversion
# ---------------------------------------------------------------------------

def _coerce_int(value) -> int:
    """Convert value to int, accepting integral floats and integer strings."""
    if isinstance(value, bool):
        raise ValueError("expected an integer, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and INTEGER_PATTERN.fullmatch(value.strip()):
        return int(value.strip())
    raise ValueError(f"expected an integer, got {value!r}")


def _coerce_float(value) -> float:
    """Convert value to a finite float, accepting numeric strings."""
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        try:
            result = float(value.strip())
        except ValueError:
            raise ValueError(f"expected a number, got {value!r}") from None
    else:
        raise ValueError(f"expected a number, got {type(value).__name__}")
    if not math.isfinite(result):
        raise ValueError(f"expected a finite number, got {value!r}")
    return result


def _coerce_bool(value) -> bool:
    """Convert value to bool, accepting true/false, yes/no, and 1/0 strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUE_STRINGS:
            return True
        if lowered in FALSE_STRINGS:
            return False
    raise ValueError(f"expected a boolean, got {value!r}")


def _coerce_date(value) -> str:
    """Validate an ISO date string (YYYY-MM-DD) and return it in canonical form."""
    if not isinstance(value, str):
        raise ValueError(f"expected a date string, got {type(value).__name__}")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        raise ValueError(f"invalid date {value!r}, expected YYYY-MM-DD") from None


def _coerce_timestamp(value) -> str:
    """Validate an ISO 8601 timestamp string and return it in canonical form."""
    if not isinstance(value, str):
        raise ValueError(f"expected a timestamp string, got {type(value).__name__}")
    try:
        # Python 3.10 fromisoformat does not accept a trailing Z
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).isoformat()
    except ValueError:
        raise ValueError(f"invalid timestamp {value!r}, expected ISO 8601") from None


def _coerce_string(value) -> str:
    """Accept only strings, so numbers are not silently stored as text."""
    if not isinstance(value, str):
        raise ValueError(f"expected a string, got {type(value).__name__}")
    return value


COERCER_DICT = {
    "STRING": _coerce_string,
    "INT64": _coerce_int,
    "FLOAT64": _coerce_float,
    "BOOL": _coerce_bool,
    "DATE": _coerce_date,
    "TIMESTAMP": _coerce_timestamp,
}


def coerce_value(value, field_type: str):
    """
    Convert a value to the JSON representation BigQuery expects for field_type.

    Parameters
    ----------
    value : object
        Value from a parsed JSON record or CSV cell. Must not be None.
    field_type : str
        Any non-RECORD type in SUPPORTED_TYPES.

    Returns
    -------
    converted : object
        int, float, bool, or str. DATE and TIMESTAMP stay ISO strings.
    """
    return COERCER_DICT[field_type](value)


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------

def _validate_value(value, field: dict, label: str):
    """Validate one non-null value, recursing into RECORD fields."""
    if field["type"] == "RECORD":
        return validate_record(value, field["fields"], f"{label}.")

    try:
        clean_value = coerce_value(value, field["type"])
    except ValueError as err:
        return None, [f"{label}: {err}"]

    error_list = []
    if "allowed_values" in field and clean_value not in field["allowed_values"]:
        error_list.append(f"{label}: {clean_value!r} is not one of {field['allowed_values']}")
    if "minimum" in field and clean_value < field["minimum"]:
        error_list.append(f"{label}: {clean_value} is below the minimum of {field['minimum']}")
    return clean_value, error_list


def _validate_repeated(value, field: dict, label: str):
    """Validate a REPEATED field; a missing value becomes an empty list."""
    if value is None:
        return [], []
    if not isinstance(value, list):
        return None, [f"{label}: expected a list"]

    item_list = []
    error_list = []
    for i, item in enumerate(value):
        clean_item, item_error_list = _validate_value(item, field, f"{label}[{i}]")
        item_list.append(clean_item)
        error_list.extend(item_error_list)
    return item_list, error_list


def validate_record(record, field_list: list, path: str = ""):
    """
    Validate one record against a list of field definitions.

    Parameters
    ----------
    record : object
        Parsed record; anything other than a dict is reported as an error.
    field_list : list of dict
        Field definitions from the validated configuration.
    path : str
        Prefix for error labels of nested fields, such as "items[0].".

    Returns
    -------
    clean_record : dict or None
        Record with converted values and every schema field present.
    error_list : list of str
        Problems found; empty if the record is valid.
    """
    if not isinstance(record, dict):
        return None, [f"{path.rstrip('.') or 'record'}: expected an object"]

    known_set = {f["name"] for f in field_list}
    error_list = [f"{path}{key}: unknown field" for key in record if key not in known_set]
    clean_record = {}

    for field in field_list:
        name = field["name"]
        label = f"{path}{name}"
        value = record.get(name)

        if field["mode"] == "REPEATED":
            clean_record[name], field_error_list = _validate_repeated(value, field, label)
        elif value is None:
            clean_record[name] = None
            missing = field["mode"] == "REQUIRED"
            field_error_list = [f"{label}: required field is missing"] if missing else []
        else:
            clean_record[name], field_error_list = _validate_value(value, field, label)
        error_list.extend(field_error_list)

    return clean_record, error_list


def validate_records(record_list: list, field_list: list) -> list:
    """
    Validate every record in a file; the file is rejected if any record is invalid.

    Parameters
    ----------
    record_list : list of dict
        Output of parse_records.
    field_list : list of dict
        Field definitions for the matched table.

    Returns
    -------
    clean_record_list : list of dict
        Records with converted values, ready to load.
    """
    clean_record_list = []
    error_list = []
    for index, record in enumerate(record_list, start=1):
        clean_record, record_error_list = validate_record(record, field_list)
        clean_record_list.append(clean_record)
        error_list.extend(f"record {index}: {error}" for error in record_error_list)

    if error_list:
        raise FileValidationError(error_list)
    return clean_record_list
