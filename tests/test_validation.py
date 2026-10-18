"""
Tests for file parsing, type conversion, and record validation.
"""

import math
from pathlib import Path

import pytest

from etl.config import load_config
from etl.errors import FileValidationError
from etl.validation import coerce_value, parse_records, validate_record, validate_records

REPO_ROOT = Path(__file__).resolve().parent.parent
TABLE_DICT = {t["name"]: t for t in load_config(REPO_ROOT / "schemas.yaml")}
ORDER_SCHEMA = TABLE_DICT["orders"]["schema"]


# ---------------------------------------------------------------------------
# Type conversion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, field_type, expected",
    [
        ("abc", "STRING", "abc"),
        (5, "INT64", 5),
        (5.0, "INT64", 5),
        (" -12 ", "INT64", -12),
        (2, "FLOAT64", 2.0),
        ("3.5", "FLOAT64", 3.5),
        (True, "BOOL", True),
        ("TRUE", "BOOL", True),
        ("no", "BOOL", False),
        ("0", "BOOL", False),
        ("2026-10-01", "DATE", "2026-10-01"),
        ("2026-10-01T08:30:00Z", "TIMESTAMP", "2026-10-01T08:30:00+00:00"),
    ],
)
def test_coerce_value(value, field_type, expected):
    assert coerce_value(value, field_type) == expected


@pytest.mark.parametrize(
    "value, field_type",
    [
        (5, "STRING"),
        (True, "INT64"),
        (2.5, "INT64"),
        ("2.5", "INT64"),
        ("abc", "FLOAT64"),
        ("nan", "FLOAT64"),
        (math.inf, "FLOAT64"),
        (False, "FLOAT64"),
        ("maybe", "BOOL"),
        (1, "BOOL"),
        ("2026-13-45", "DATE"),
        (20261001, "DATE"),
        ("yesterday", "TIMESTAMP"),
    ],
)
def test_coerce_value_rejects_invalid(value, field_type):
    with pytest.raises(ValueError):
        coerce_value(value, field_type)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_parse_ndjson_skips_blank_lines():
    content = b'{"a": 1}\n\n{"a": 2}\n'
    assert parse_records(content, "NEWLINE_DELIMITED_JSON") == [{"a": 1}, {"a": 2}]


@pytest.mark.parametrize(
    "content, expected_fragment",
    [
        (b'{"a": 1}\n{"a": \n', "line 2: invalid JSON"),
        (b'[1, 2]\n', "line 1: expected a JSON object"),
        (b"", "no records"),
        (b"\n\n", "no records"),
        (b"\xff\xfe\x00", "not valid UTF-8"),
    ],
)
def test_parse_ndjson_errors(content, expected_fragment):
    with pytest.raises(FileValidationError) as excinfo:
        parse_records(content, "NEWLINE_DELIMITED_JSON")
    assert expected_fragment in excinfo.value.error_list[0]


def test_parse_csv_converts_empty_cells_to_none_and_strips_bom():
    content = "\ufeffid,email\n1,\n2,b@example.com\n".encode("utf-8")
    assert parse_records(content, "CSV") == [
        {"id": "1", "email": None},
        {"id": "2", "email": "b@example.com"},
    ]


@pytest.mark.parametrize(
    "content, expected_fragment",
    [
        (b"id,email\n1,a@example.com,extra\n", "line 2: more values than header"),
        (b"", "no records"),
        (b"id,email\n", "no records"),
    ],
)
def test_parse_csv_errors(content, expected_fragment):
    with pytest.raises(FileValidationError) as excinfo:
        parse_records(content, "CSV")
    assert expected_fragment in excinfo.value.error_list[0]


# ---------------------------------------------------------------------------
# Record validation
# ---------------------------------------------------------------------------

def make_order(**overrides):
    """Return a valid order record with selected fields replaced."""
    order = {
        "order_id": "ORD-1",
        "customer_id": "CUST-1",
        "order_date": "2026-10-01",
        "status": "pending",
        "items": [{"sku": "SKU-1", "quantity": 1, "unit_price": 2.5}],
    }
    order.update(overrides)
    return order


def test_valid_record_gets_every_schema_field():
    clean_record, error_list = validate_record(make_order(), ORDER_SCHEMA)
    assert error_list == []
    assert clean_record["shipping"] is None


def test_missing_repeated_field_becomes_empty_list():
    order = make_order()
    del order["items"]
    clean_record, error_list = validate_record(order, ORDER_SCHEMA)
    assert error_list == []
    assert clean_record["items"] == []


@pytest.mark.parametrize(
    "overrides, expected_error",
    [
        ({"customer_id": None}, "customer_id: required field is missing"),
        ({"status": "lost"}, "status: 'lost' is not one of"),
        ({"order_date": "10/01/2026"}, "order_date: invalid date"),
        ({"coupon": "FALL10"}, "coupon: unknown field"),
        ({"items": "SKU-1"}, "items: expected a list"),
        ({"items": ["SKU-1"]}, "items[0]: expected an object"),
        ({"items": [{"sku": "A", "quantity": 0, "unit_price": 1}]}, "items[0].quantity: 0 is"),
        ({"items": [{"sku": "A", "quantity": 1}]}, "items[0].unit_price: required field"),
        ({"shipping": {"city": "Boston", "zip": 2115}}, "shipping.zip: expected a string"),
    ],
)
def test_invalid_record_errors(overrides, expected_error):
    _, error_list = validate_record(make_order(**overrides), ORDER_SCHEMA)
    assert any(expected_error in error for error in error_list), error_list


def test_validate_records_reports_record_numbers():
    with pytest.raises(FileValidationError) as excinfo:
        validate_records([make_order(), make_order(status="lost")], ORDER_SCHEMA)
    assert excinfo.value.error_list[0].startswith("record 2: status")


# ---------------------------------------------------------------------------
# Sample data shipped with the repository
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "file_name, table_name, expected_count",
    [("orders_2026-10-01.json", "orders", 5), ("customers_2026-10-01.csv", "customers", 4)],
)
def test_valid_sample_files(file_name, table_name, expected_count):
    table = TABLE_DICT[table_name]
    content = (REPO_ROOT / "sample_data" / file_name).read_bytes()
    record_list = validate_records(parse_records(content, table["format"]), table["schema"])
    assert len(record_list) == expected_count


def test_bad_sample_file_reports_every_problem():
    content = (REPO_ROOT / "sample_data" / "orders_2026-10-02_bad.json").read_bytes()
    with pytest.raises(FileValidationError) as excinfo:
        validate_records(parse_records(content, "NEWLINE_DELIMITED_JSON"), ORDER_SCHEMA)
    assert len(excinfo.value.error_list) == 6
