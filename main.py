"""
Cloud Function entry point that loads files uploaded to Cloud Storage into BigQuery.
Triggered by google.cloud.storage.object.v1.finalized events on the upload bucket.
"""

import os
from pathlib import Path

import functions_framework

from etl.config import load_config
from etl.gcp import BigQueryWarehouse, GcsStorage
from etl.pipeline import process_file

CONFIG_PATH = Path(__file__).resolve().parent / "schemas.yaml"
BQ_DATASET = os.environ.get("BQ_DATASET", "ecommerce")

# validated at cold start, so a broken schemas.yaml fails immediately and visibly
TABLE_LIST = load_config(CONFIG_PATH)

# clients are reused across invocations of a warm instance, as Google recommends
adapter_dict = {}


def get_adapters():
    """Create the storage and warehouse adapters on first use and reuse them afterwards."""
    if not adapter_dict:
        adapter_dict["storage"] = GcsStorage()
        adapter_dict["warehouse"] = BigQueryWarehouse(BQ_DATASET)
    return adapter_dict["storage"], adapter_dict["warehouse"]


@functions_framework.cloud_event
def process_upload(cloud_event):
    """
    Handle one Cloud Storage object finalized event.

    Parameters
    ----------
    cloud_event : cloudevents.http.CloudEvent
        Event whose data contains the bucket and object name.
    """
    data = cloud_event.data
    print(f"event {cloud_event['id']}: gs://{data['bucket']}/{data['name']}")
    storage, warehouse = get_adapters()
    process_file(
        data["bucket"], data["name"], TABLE_LIST, storage, warehouse,
        event_id=cloud_event["id"],
    )
