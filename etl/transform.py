"""
Adds audit columns to validated records and defines the ingestion log written for every file.
"""

from copy import deepcopy

AUDIT_FIELD_LIST = [
    {"name": "ingested_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    {"name": "source_file", "type": "STRING", "mode": "REQUIRED"},
]

INGESTION_LOG_TABLE = "ingestion_log"
INGESTION_LOG_SCHEMA = [
    {"name": "processed_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    {"name": "event_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "source_file", "type": "STRING", "mode": "REQUIRED"},
    {"name": "table_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "content_md5", "type": "STRING", "mode": "NULLABLE"},
    {"name": "status", "type": "STRING", "mode": "REQUIRED"},
    {"name": "rows_loaded", "type": "INT64", "mode": "REQUIRED"},
    {"name": "error_count", "type": "INT64", "mode": "REQUIRED"},
    {"name": "message", "type": "STRING", "mode": "NULLABLE"},
]

STATUS_LOADED = "LOADED"
STATUS_QUARANTINED = "QUARANTINED"
STATUS_SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"
STATUS_IGNORED = "IGNORED"
STATUS_FAILED = "FAILED"

BIGQUERY_FIELD_KEYS = ("name", "type", "mode")


def to_bigquery_schema(field_list: list) -> list:
    """
    Convert configured fields to the BigQuery API schema format.

    Validation-only keys such as allowed_values and minimum are removed, since BigQuery
    rejects unknown schema properties.

    Parameters
    ----------
    field_list : list of dict
        Field definitions from the validated configuration.

    Returns
    -------
    api_schema : list of dict
        Fields with only name, type, mode, and nested fields.
    """
    api_schema = []
    for field in field_list:
        api_field = {key: field[key] for key in BIGQUERY_FIELD_KEYS}
        if "fields" in field:
            api_field["fields"] = to_bigquery_schema(field["fields"])
        api_schema.append(api_field)
    return api_schema


def build_table_schema(field_list: list) -> list:
    """Return the BigQuery schema for a data table, with audit columns appended."""
    return to_bigquery_schema(field_list) + deepcopy(AUDIT_FIELD_LIST)


def add_audit_columns(record_list: list, source_uri: str, ingested_at) -> list:
    """
    Return copies of records with ingested_at and source_file added.

    Parameters
    ----------
    record_list : list of dict
        Validated records; not modified.
    source_uri : str
        gs:// URI of the uploaded file.
    ingested_at : datetime.datetime
        Timezone-aware processing time shared by every row of the file.

    Returns
    -------
    row_list : list of dict
        Rows ready to load into BigQuery.
    """
    timestamp = ingested_at.isoformat()
    return [
        {**record, "ingested_at": timestamp, "source_file": source_uri}
        for record in record_list
    ]


def build_log_entry(source_uri, status, processed_at, event_id="", table_name=None,
                    content_md5=None, rows_loaded=0, error_count=0, message=None):
    """
    Build one ingestion log row describing what happened to an uploaded file.

    Parameters
    ----------
    source_uri : str
        gs:// URI of the uploaded file.
    status : str
        One of the STATUS_* constants.
    processed_at : datetime.datetime
        Timezone-aware processing time.
    event_id : str
        Cloud event ID, used to trace the entry back to the function logs.
    table_name : str or None
        Matched table, or None if no table matched.
    content_md5 : str or None
        Hex MD5 of the file content, used for duplicate detection.
    rows_loaded : int
        Number of rows loaded into BigQuery.
    error_count : int
        Number of validation errors found.
    message : str or None
        Short human-readable explanation.

    Returns
    -------
    log_entry : dict
        Row matching INGESTION_LOG_SCHEMA.
    """
    return {
        "processed_at": processed_at.isoformat(),
        "event_id": event_id,
        "source_file": source_uri,
        "table_name": table_name,
        "content_md5": content_md5,
        "status": status,
        "rows_loaded": rows_loaded,
        "error_count": error_count,
        "message": message,
    }
