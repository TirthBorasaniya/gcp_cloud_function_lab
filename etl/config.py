"""
Loads table definitions from schemas.yaml, validates them, and matches uploaded files to tables.
"""

import fnmatch
from pathlib import PurePosixPath

import yaml

from etl.errors import ConfigError

SUPPORTED_FORMATS = ("NEWLINE_DELIMITED_JSON", "CSV")
SUPPORTED_TYPES = ("STRING", "INT64", "FLOAT64", "BOOL", "DATE", "TIMESTAMP", "RECORD")
SUPPORTED_MODES = ("NULLABLE", "REQUIRED", "REPEATED")
NUMERIC_TYPES = ("INT64", "FLOAT64")
RESERVED_COLUMNS = ("ingested_at", "source_file")
DEFAULT_MODE = "NULLABLE"
REQUIRED_TABLE_KEYS = ("name", "file_pattern", "format", "schema")


def load_config(config_path) -> list:
    """
    Read and validate the table configuration file.

    Parameters
    ----------
    config_path : str or pathlib.Path
        Path to schemas.yaml.

    Returns
    -------
    table_list : list of dict
        Validated table definitions, each with keys name, file_pattern, format, and schema.
    """
    with open(config_path) as f:
        try:
            raw_config = yaml.safe_load(f)
        except yaml.YAMLError as err:
            raise ConfigError(f"invalid YAML in {config_path}: {err}") from err
    return validate_config(raw_config)


def validate_config(raw_config) -> list:
    """
    Validate a parsed configuration and normalize field definitions.

    Parameters
    ----------
    raw_config : dict
        Parsed YAML with a top-level "tables" list.

    Returns
    -------
    table_list : list of dict
        Validated table definitions with every field's mode filled in.
    """
    if not isinstance(raw_config, dict) or not isinstance(raw_config.get("tables"), list):
        raise ConfigError("config must contain a 'tables' list")
    if not raw_config["tables"]:
        raise ConfigError("'tables' must not be empty")

    table_list = [_validate_table(table, i) for i, table in enumerate(raw_config["tables"])]
    _check_unique([t["name"] for t in table_list], "table names")
    return table_list


def _check_unique(name_list: list, label: str) -> None:
    """Raise ConfigError if name_list contains duplicates."""
    duplicate_set = {name for name in name_list if name_list.count(name) > 1}
    if duplicate_set:
        raise ConfigError(f"duplicate {label}: {sorted(duplicate_set)}")


def _validate_table(table, index: int) -> dict:
    """Validate one table definition and return its normalized form."""
    if not isinstance(table, dict):
        raise ConfigError(f"tables[{index}] must be a mapping")
    missing_list = [key for key in REQUIRED_TABLE_KEYS if key not in table]
    if missing_list:
        raise ConfigError(f"tables[{index}] is missing {missing_list}")

    name = table["name"]
    if table["format"] not in SUPPORTED_FORMATS:
        raise ConfigError(f"{name}: format must be one of {SUPPORTED_FORMATS}")

    schema = _validate_fields(table["schema"], f"{name}.schema")
    if table["format"] == "CSV" and any(
        f["type"] == "RECORD" or f["mode"] == "REPEATED" for f in schema
    ):
        raise ConfigError(f"{name}: CSV tables cannot contain RECORD or REPEATED fields")

    reserved_set = {f["name"] for f in schema} & set(RESERVED_COLUMNS)
    if reserved_set:
        raise ConfigError(f"{name}: {sorted(reserved_set)} are reserved for audit columns")

    return {
        "name": name,
        "file_pattern": table["file_pattern"],
        "format": table["format"],
        "schema": schema,
    }


def _validate_fields(field_list, label: str) -> list:
    """Validate a list of field definitions recursively and fill in default modes."""
    if not isinstance(field_list, list) or not field_list:
        raise ConfigError(f"{label} must be a non-empty list")

    validated_list = []
    for field in field_list:
        if not isinstance(field, dict) or "name" not in field or "type" not in field:
            raise ConfigError(f"{label}: every field needs 'name' and 'type'")
        validated_list.append(_validate_field(field, f"{label}.{field['name']}"))

    _check_unique([f["name"] for f in validated_list], f"fields in {label}")
    return validated_list


def _validate_field(field: dict, label: str) -> dict:
    """Validate one field definition, including optional validation rules."""
    field_type = field["type"]
    mode = field.get("mode", DEFAULT_MODE)
    if field_type not in SUPPORTED_TYPES:
        raise ConfigError(f"{label}: type must be one of {SUPPORTED_TYPES}")
    if mode not in SUPPORTED_MODES:
        raise ConfigError(f"{label}: mode must be one of {SUPPORTED_MODES}")

    clean_field = {"name": field["name"], "type": field_type, "mode": mode}
    if field_type == "RECORD":
        clean_field["fields"] = _validate_fields(field.get("fields"), label)

    if "allowed_values" in field:
        allowed_values = field["allowed_values"]
        if not isinstance(allowed_values, list) or not allowed_values:
            raise ConfigError(f"{label}: allowed_values must be a non-empty list")
        clean_field["allowed_values"] = list(allowed_values)

    if "minimum" in field:
        minimum = field["minimum"]
        if field_type not in NUMERIC_TYPES:
            raise ConfigError(f"{label}: minimum applies only to {NUMERIC_TYPES}")
        if isinstance(minimum, bool) or not isinstance(minimum, (int, float)):
            raise ConfigError(f"{label}: minimum must be a number")
        clean_field["minimum"] = minimum

    return clean_field


def match_table(file_name: str, table_list: list):
    """
    Find the table whose file_pattern matches the uploaded file's name.

    Matching uses shell-style wildcards on the file name only, so folders in the object
    path are ignored and "orders_*.json" does not match "orders_x.csv".

    Parameters
    ----------
    file_name : str
        Object name within the bucket, possibly including folders.
    table_list : list of dict
        Validated table definitions.

    Returns
    -------
    table : dict or None
        Matching table definition, or None if no pattern matches.
    """
    base_name = PurePosixPath(file_name).name
    match_list = [t for t in table_list if fnmatch.fnmatchcase(base_name, t["file_pattern"])]
    if len(match_list) > 1:
        raise ConfigError(
            f"{file_name} matches several tables: {[t['name'] for t in match_list]}"
        )
    return match_list[0] if match_list else None
