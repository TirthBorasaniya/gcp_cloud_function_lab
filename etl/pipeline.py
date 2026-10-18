"""
Processes one uploaded file: match, deduplicate, validate, transform, load, and log.
Storage and warehouse adapters are passed in, so the pipeline runs without GCP in tests.
"""

import hashlib
from datetime import datetime, timezone
from functools import partial

from etl.config import match_table
from etl.errors import FileValidationError, LoadError
from etl.transform import (
    STATUS_FAILED,
    STATUS_IGNORED,
    STATUS_LOADED,
    STATUS_QUARANTINED,
    STATUS_SKIPPED_DUPLICATE,
    add_audit_columns,
    build_log_entry,
    build_table_schema,
)
from etl.validation import parse_records, validate_records

QUARANTINE_PREFIX = "quarantine/"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_REPORTED_ERRORS = 20
MAX_MESSAGE_ERRORS = 3


def _record(warehouse, log_entry: dict) -> dict:
    """Print a one-line summary, write the entry to the ingestion log, and return it."""
    print(f"{log_entry['status']}: {log_entry['source_file']} ({log_entry['message'] or 'ok'})")
    warehouse.log_ingestion(log_entry)
    return log_entry


def _quarantine(storage, warehouse, bucket_name, file_name, error_list, make_entry):
    """Move a rejected file to the quarantine folder with an error report, then log it."""
    report_dict = {
        "source_file": f"gs://{bucket_name}/{file_name}",
        "error_count": len(error_list),
        "errors": error_list[:MAX_REPORTED_ERRORS],
    }
    storage.quarantine(bucket_name, file_name, report_dict)
    log_entry = make_entry(
        STATUS_QUARANTINED,
        error_count=len(error_list),
        message="; ".join(error_list[:MAX_MESSAGE_ERRORS]),
    )
    return _record(warehouse, log_entry)


def process_file(bucket_name, file_name, table_list, storage, warehouse,
                 event_id="", now=None, max_file_bytes=MAX_FILE_BYTES):
    """
    Run the full ETL flow for one object uploaded to Cloud Storage.

    Expected outcomes (validation failures, duplicates, unmatched names) are logged and
    return normally. A BigQuery load failure is logged and re-raised so the function run
    is reported as failed.

    Parameters
    ----------
    bucket_name : str
        Bucket that received the upload.
    file_name : str
        Object name within the bucket.
    table_list : list of dict
        Validated table definitions from schemas.yaml.
    storage : object
        Adapter with get_size, download, and quarantine methods.
    warehouse : object
        Adapter with is_duplicate, load_rows, and log_ingestion methods.
    event_id : str
        Cloud event ID, recorded in the ingestion log.
    now : datetime.datetime or None
        Processing time; defaults to the current UTC time.
    max_file_bytes : int
        Files larger than this are quarantined without being downloaded.

    Returns
    -------
    log_entry : dict or None
        Ingestion log entry for the file, or None for objects in the quarantine folder.
    """
    # quarantined files are written by this function, so processing them would loop
    if file_name.startswith(QUARANTINE_PREFIX):
        print(f"skipping {file_name}: already in quarantine")
        return None

    now = now or datetime.now(timezone.utc)
    source_uri = f"gs://{bucket_name}/{file_name}"
    table = match_table(file_name, table_list)
    make_entry = partial(
        build_log_entry, source_uri, processed_at=now, event_id=event_id,
        table_name=table["name"] if table else None,
    )

    if table is None:
        log_entry = make_entry(STATUS_IGNORED, message="no table matches this file name")
        return _record(warehouse, log_entry)

    size = storage.get_size(bucket_name, file_name)
    if size > max_file_bytes:
        error_list = [f"file is {size} bytes, limit is {max_file_bytes}"]
        return _quarantine(storage, warehouse, bucket_name, file_name, error_list, make_entry)

    content = storage.download(bucket_name, file_name)
    content_md5 = hashlib.md5(content).hexdigest()
    make_entry = partial(make_entry, content_md5=content_md5)

    if warehouse.is_duplicate(table["name"], content_md5):
        log_entry = make_entry(
            STATUS_SKIPPED_DUPLICATE, message="identical content was already loaded"
        )
        return _record(warehouse, log_entry)

    try:
        clean_record_list = validate_records(
            parse_records(content, table["format"]), table["schema"]
        )
    except FileValidationError as err:
        return _quarantine(storage, warehouse, bucket_name, file_name, err.error_list, make_entry)

    row_list = add_audit_columns(clean_record_list, source_uri, now)
    try:
        warehouse.load_rows(table["name"], build_table_schema(table["schema"]), row_list)
    except LoadError as err:
        _record(warehouse, make_entry(STATUS_FAILED, message=str(err)))
        raise

    return _record(warehouse, make_entry(STATUS_LOADED, rows_loaded=len(row_list)))
