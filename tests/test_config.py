"""
Tests for loading and validating schemas.yaml and matching file names to tables.
"""

from copy import deepcopy
from pathlib import Path

import pytest

from etl.config import load_config, match_table, validate_config
from etl.errors import ConfigError

CONFIG_PATH = Path(__file__).resolve().parent.parent / "schemas.yaml"
VALID_CONFIG = {
    "tables": [
        {
            "name": "orders",
            "file_pattern": "orders_*.json",
            "format": "NEWLINE_DELIMITED_JSON",
            "schema": [{"name": "order_id", "type": "STRING", "mode": "REQUIRED"}],
        }
    ]
}


def make_config(**table_overrides):
    """Return a copy of VALID_CONFIG with keys of the single table replaced."""
    config = deepcopy(VALID_CONFIG)
    config["tables"][0].update(table_overrides)
    return config


def test_repository_config_is_valid():
    table_list = load_config(CONFIG_PATH)
    assert [t["name"] for t in table_list] == ["orders", "customers"]


def test_default_mode_is_filled_in():
    config = make_config(schema=[{"name": "note", "type": "STRING"}])
    assert validate_config(config)[0]["schema"][0]["mode"] == "NULLABLE"


@pytest.mark.parametrize(
    "config",
    [
        None,
        {"tables": []},
        {"tables": "orders"},
        {"tables": [{"name": "orders"}]},
        make_config(format="PARQUET"),
        make_config(schema=[]),
        make_config(schema=[{"name": "x", "type": "VARCHAR"}]),
        make_config(schema=[{"name": "x", "type": "STRING", "mode": "OPTIONAL"}]),
        make_config(schema=[{"name": "x", "type": "STRING"}, {"name": "x", "type": "INT64"}]),
        make_config(schema=[{"name": "ingested_at", "type": "TIMESTAMP"}]),
        make_config(schema=[{"name": "r", "type": "RECORD"}]),
        make_config(schema=[{"name": "x", "type": "STRING", "minimum": 1}]),
        make_config(schema=[{"name": "x", "type": "INT64", "minimum": "1"}]),
        make_config(schema=[{"name": "x", "type": "STRING", "allowed_values": []}]),
        make_config(format="CSV", schema=[
            {"name": "r", "type": "RECORD", "fields": [{"name": "a", "type": "STRING"}]}
        ]),
        make_config(format="CSV", schema=[{"name": "tags", "type": "STRING", "mode": "REPEATED"}]),
        {"tables": [VALID_CONFIG["tables"][0], VALID_CONFIG["tables"][0]]},
    ],
)
def test_invalid_config_is_rejected(config):
    with pytest.raises(ConfigError):
        validate_config(config)


def test_invalid_yaml_is_rejected(tmp_path):
    config_path = tmp_path / "schemas.yaml"
    config_path.write_text("tables: [unclosed")
    with pytest.raises(ConfigError):
        load_config(config_path)


@pytest.mark.parametrize(
    "file_name, expected_table",
    [
        ("orders_2026-10-01.json", "orders"),
        ("incoming/orders_2026-10-01.json", "orders"),
        ("customers_2026-10-01.csv", "customers"),
        ("orders_2026-10-01.csv", None),
        ("orders.json", None),
        ("my_orders_2026.json", None),
        ("ORDERS_2026.json", None),
    ],
)
def test_match_table(file_name, expected_table):
    table = match_table(file_name, load_config(CONFIG_PATH))
    assert (table["name"] if table else None) == expected_table


def test_ambiguous_match_is_rejected():
    table_list = validate_config(VALID_CONFIG)
    duplicate = dict(table_list[0], name="orders_copy")
    with pytest.raises(ConfigError):
        match_table("orders_1.json", table_list + [duplicate])
