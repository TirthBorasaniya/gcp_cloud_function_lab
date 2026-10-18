"""
In-memory stand-ins for the Cloud Storage and BigQuery adapters used in pipeline tests.
"""

from etl.errors import LoadError
from etl.transform import STATUS_LOADED


class FakeStorage:
    """Bucket held in a dict mapping object names to bytes."""

    def __init__(self, object_dict):
        self.object_dict = dict(object_dict)
        self.quarantine_dict = {}

    def get_size(self, bucket_name, file_name):
        return len(self.object_dict[file_name])

    def download(self, bucket_name, file_name):
        return self.object_dict[file_name]

    def quarantine(self, bucket_name, file_name, report_dict):
        self.quarantine_dict[file_name] = report_dict
        del self.object_dict[file_name]


class FakeWarehouse:
    """Dataset held in memory; optionally fails every data load."""

    def __init__(self, o_fail_loads=False):
        self.o_fail_loads = o_fail_loads
        self.rows_per_table = {}
        self.schema_dict = {}
        self.log_list = []

    def load_rows(self, table_name, api_schema, row_list):
        if self.o_fail_loads:
            raise LoadError(f"simulated failure loading {table_name}")
        self.schema_dict[table_name] = api_schema
        self.rows_per_table.setdefault(table_name, []).extend(row_list)

    def is_duplicate(self, table_name, content_md5):
        return any(
            entry["table_name"] == table_name
            and entry["content_md5"] == content_md5
            and entry["status"] == STATUS_LOADED
            for entry in self.log_list
        )

    def log_ingestion(self, log_entry):
        self.log_list.append(log_entry)
