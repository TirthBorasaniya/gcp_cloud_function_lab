"""
Tests for schema conversion, audit columns, and ingestion log entries.
"""

from datetime import datetime, timezone

from etl.transform import (
    INGESTION_LOG_SCHEMA,
    STATUS_LOADED,
    add_audit_columns,
    build_log_entry,
    build_table_schema,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_build_table_schema_strips_rules_and_adds_audit_columns():
    field_list = [
        {"name": "qty", "type": "INT64", "mode": "REQUIRED", "minimum": 1},
        {"name": "r", "type": "RECORD", "mode": "NULLABLE",
         "fields": [{"name": "s", "type": "STRING", "mode": "NULLABLE", "allowed_values": ["a"]}]},
    ]
    schema = build_table_schema(field_list)
    assert schema[0] == {"name": "qty", "type": "INT64", "mode": "REQUIRED"}
    assert schema[1]["fields"] == [{"name": "s", "type": "STRING", "mode": "NULLABLE"}]
    assert [f["name"] for f in schema[-2:]] == ["ingested_at", "source_file"]
    assert "minimum" in field_list[0]


def test_add_audit_columns_does_not_modify_input():
    record_list = [{"id": 1}]
    row_list = add_audit_columns(record_list, "gs://b/f.json", NOW)
    assert row_list == [
        {"id": 1, "ingested_at": "2026-10-01T12:00:00+00:00", "source_file": "gs://b/f.json"}
    ]
    assert record_list == [{"id": 1}]


def test_log_entry_matches_log_schema():
    log_entry = build_log_entry("gs://b/f.json", STATUS_LOADED, NOW, rows_loaded=3)
    assert list(log_entry) == [f["name"] for f in INGESTION_LOG_SCHEMA]
    assert log_entry["rows_loaded"] == 3
