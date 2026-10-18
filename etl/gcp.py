"""
Adapters connecting the pipeline to Cloud Storage and BigQuery.
These are the only parts of the code that call Google Cloud APIs.
"""

import json

from google.api_core import exceptions as google_exceptions
from google.cloud import bigquery, storage

from etl.errors import LoadError
from etl.pipeline import QUARANTINE_PREFIX
from etl.transform import INGESTION_LOG_SCHEMA, INGESTION_LOG_TABLE, STATUS_LOADED

REPORT_SUFFIX = ".error.json"
DUPLICATE_QUERY = """
SELECT COUNT(*) AS match_count
FROM `{log_table_id}`
WHERE table_name = @table_name AND content_md5 = @content_md5 AND status = @status
"""


class GcsStorage:
    """Reads uploaded objects and moves rejected ones to the quarantine folder."""

    def __init__(self, client=None):
        self.client = client or storage.Client()

    def get_size(self, bucket_name: str, file_name: str) -> int:
        """Return the object size in bytes without downloading it."""
        blob = self.client.bucket(bucket_name).get_blob(file_name)
        if blob is None:
            raise FileNotFoundError(f"gs://{bucket_name}/{file_name} no longer exists")
        return blob.size

    def download(self, bucket_name: str, file_name: str) -> bytes:
        """Return the object content."""
        return self.client.bucket(bucket_name).blob(file_name).download_as_bytes()

    def quarantine(self, bucket_name: str, file_name: str, report_dict: dict) -> None:
        """Move the object under QUARANTINE_PREFIX and write a JSON error report beside it."""
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        quarantine_name = f"{QUARANTINE_PREFIX}{file_name}"
        bucket.copy_blob(blob, bucket, quarantine_name)
        blob.delete()
        bucket.blob(f"{quarantine_name}{REPORT_SUFFIX}").upload_from_string(
            json.dumps(report_dict, indent=2), content_type="application/json"
        )


class BigQueryWarehouse:
    """Creates tables on first use, loads rows with batch load jobs, and keeps the ingestion log."""

    def __init__(self, dataset_id: str, client=None):
        self.client = client or bigquery.Client()
        self.dataset_id = dataset_id
        self.ensured_table_set = set()

    def _table_id(self, table_name: str) -> str:
        """Return the fully qualified table ID."""
        return f"{self.client.project}.{self.dataset_id}.{table_name}"

    def _ensure_table(self, table_name: str, api_schema: list) -> None:
        """Create the table if it does not exist; checked once per function instance."""
        if table_name in self.ensured_table_set:
            return
        schema = [bigquery.SchemaField.from_api_repr(field) for field in api_schema]
        self.client.create_table(bigquery.Table(self._table_id(table_name), schema=schema),
                                 exists_ok=True)
        self.ensured_table_set.add(table_name)

    def load_rows(self, table_name: str, api_schema: list, row_list: list) -> None:
        """
        Append rows to a table with a batch load job.

        Batch load jobs are free, unlike streaming inserts, and a fresh job configuration
        is built for every call instead of sharing one module-level object.

        Parameters
        ----------
        table_name : str
            Table within the dataset.
        api_schema : list of dict
            BigQuery API schema, including audit columns.
        row_list : list of dict
            JSON-serializable rows.
        """
        self._ensure_table(table_name, api_schema)
        job_config = bigquery.LoadJobConfig(
            schema=[bigquery.SchemaField.from_api_repr(field) for field in api_schema],
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        )
        try:
            self.client.load_table_from_json(
                row_list, self._table_id(table_name), job_config=job_config
            ).result()
        except google_exceptions.GoogleAPIError as err:
            raise LoadError(f"BigQuery load into {table_name} failed: {err}") from err

    def is_duplicate(self, table_name: str, content_md5: str) -> bool:
        """Return True if identical content was already loaded into this table."""
        self._ensure_table(INGESTION_LOG_TABLE, INGESTION_LOG_SCHEMA)
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("table_name", "STRING", table_name),
            bigquery.ScalarQueryParameter("content_md5", "STRING", content_md5),
            bigquery.ScalarQueryParameter("status", "STRING", STATUS_LOADED),
        ])
        query = DUPLICATE_QUERY.format(log_table_id=self._table_id(INGESTION_LOG_TABLE))
        row_list = list(self.client.query(query, job_config=job_config).result())
        return row_list[0]["match_count"] > 0

    def log_ingestion(self, log_entry: dict) -> None:
        """Append one entry to the ingestion log table."""
        self.load_rows(INGESTION_LOG_TABLE, INGESTION_LOG_SCHEMA, [log_entry])
