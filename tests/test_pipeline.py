"""
End-to-end tests of the pipeline using in-memory storage and warehouse fakes.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from etl.config import load_config
from etl.errors import LoadError
from etl.pipeline import QUARANTINE_PREFIX, process_file
from etl.transform import (
    INGESTION_LOG_TABLE,
    STATUS_FAILED,
    STATUS_IGNORED,
    STATUS_LOADED,
    STATUS_QUARANTINED,
    STATUS_SKIPPED_DUPLICATE,
)
from tests.fakes import FakeStorage, FakeWarehouse

REPO_ROOT = Path(__file__).resolve().parent.parent
TABLE_LIST = load_config(REPO_ROOT / "schemas.yaml")
BUCKET = "test-bucket"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
ORDERS_FILE = "orders_2026-10-01.json"
CUSTOMERS_FILE = "customers_2026-10-01.csv"
BAD_FILE = "orders_2026-10-02_bad.json"


def read_sample(file_name):
    """Return the bytes of a file in sample_data."""
    return (REPO_ROOT / "sample_data" / file_name).read_bytes()


def run(file_name, storage, warehouse, **kwargs):
    """Process one file with the shared table list and fixed time."""
    return process_file(BUCKET, file_name, TABLE_LIST, storage, warehouse, now=NOW, **kwargs)


def test_valid_json_file_is_loaded_with_audit_columns():
    storage = FakeStorage({ORDERS_FILE: read_sample(ORDERS_FILE)})
    warehouse = FakeWarehouse()
    log_entry = run(ORDERS_FILE, storage, warehouse, event_id="evt-1")

    assert log_entry["status"] == STATUS_LOADED
    assert log_entry["rows_loaded"] == 5
    assert log_entry["event_id"] == "evt-1"
    row_list = warehouse.rows_per_table["orders"]
    assert row_list[0]["source_file"] == f"gs://{BUCKET}/{ORDERS_FILE}"
    assert row_list[0]["ingested_at"] == NOW.isoformat()
    assert row_list[0]["shipping"]["zip"] == "02115"
    assert warehouse.schema_dict["orders"][-1]["name"] == "source_file"
    assert warehouse.log_list == [log_entry]


def test_valid_csv_file_is_loaded_with_typed_values():
    storage = FakeStorage({CUSTOMERS_FILE: read_sample(CUSTOMERS_FILE)})
    warehouse = FakeWarehouse()
    log_entry = run(CUSTOMERS_FILE, storage, warehouse)

    assert log_entry["status"] == STATUS_LOADED
    row_list = warehouse.rows_per_table["customers"]
    assert row_list[0]["is_member"] is True
    assert row_list[1]["email"] is None


def test_duplicate_content_is_skipped_even_under_a_new_name():
    content = read_sample(ORDERS_FILE)
    storage = FakeStorage({ORDERS_FILE: content, "orders_copy.json": content})
    warehouse = FakeWarehouse()
    run(ORDERS_FILE, storage, warehouse)
    log_entry = run("orders_copy.json", storage, warehouse)

    assert log_entry["status"] == STATUS_SKIPPED_DUPLICATE
    assert len(warehouse.rows_per_table["orders"]) == 5


def test_invalid_file_is_quarantined_with_report():
    storage = FakeStorage({BAD_FILE: read_sample(BAD_FILE)})
    warehouse = FakeWarehouse()
    log_entry = run(BAD_FILE, storage, warehouse)

    assert log_entry["status"] == STATUS_QUARANTINED
    assert log_entry["error_count"] == 6
    assert "orders" not in warehouse.rows_per_table
    assert BAD_FILE not in storage.object_dict
    report_dict = storage.quarantine_dict[BAD_FILE]
    assert report_dict["error_count"] == 6
    assert report_dict["errors"][0].startswith("record 2:")


def test_quarantined_content_is_not_a_duplicate_once_fixed():
    storage = FakeStorage({BAD_FILE: read_sample(BAD_FILE), ORDERS_FILE: read_sample(ORDERS_FILE)})
    warehouse = FakeWarehouse()
    run(BAD_FILE, storage, warehouse)
    assert run(ORDERS_FILE, storage, warehouse)["status"] == STATUS_LOADED


def test_oversized_file_is_quarantined_without_download():
    storage = FakeStorage({ORDERS_FILE: read_sample(ORDERS_FILE)})
    warehouse = FakeWarehouse()
    log_entry = run(ORDERS_FILE, storage, warehouse, max_file_bytes=10)

    assert log_entry["status"] == STATUS_QUARANTINED
    assert log_entry["content_md5"] is None
    assert "limit is 10" in storage.quarantine_dict[ORDERS_FILE]["errors"][0]


def test_unmatched_file_is_ignored_and_logged():
    storage = FakeStorage({"notes.txt": b"hello"})
    warehouse = FakeWarehouse()
    log_entry = run("notes.txt", storage, warehouse)

    assert log_entry["status"] == STATUS_IGNORED
    assert log_entry["table_name"] is None
    assert warehouse.rows_per_table == {}


def test_quarantine_folder_is_skipped_to_avoid_loops():
    file_name = f"{QUARANTINE_PREFIX}{BAD_FILE}"
    storage = FakeStorage({file_name: read_sample(BAD_FILE)})
    warehouse = FakeWarehouse()

    assert run(file_name, storage, warehouse) is None
    assert warehouse.log_list == []


def test_load_failure_is_logged_and_raised():
    storage = FakeStorage({ORDERS_FILE: read_sample(ORDERS_FILE)})
    warehouse = FakeWarehouse(o_fail_loads=True)
    with pytest.raises(LoadError):
        run(ORDERS_FILE, storage, warehouse)
    assert warehouse.log_list[-1]["status"] == STATUS_FAILED
    assert INGESTION_LOG_TABLE not in warehouse.rows_per_table
