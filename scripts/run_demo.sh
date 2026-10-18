#!/usr/bin/env bash
# Uploads the sample files, waits for processing, and prints the results from BigQuery and the bucket.
# Usage, from the repository root: ./scripts/run_demo.sh
set -euo pipefail
source "$(dirname "$0")/config.sh"
WAIT_SECONDS="${WAIT_SECONDS:-45}"

upload() {
  echo "==> Uploading $1 as $2"
  gcloud storage cp "sample_data/$1" "gs://$BUCKET/$2"
}

query() {
  echo
  echo "==> $1"
  bq query --use_legacy_sql=false --format=pretty "$2"
}

upload orders_2026-10-01.json orders_2026-10-01.json
upload customers_2026-10-01.csv customers_2026-10-01.csv
echo "==> Waiting ${WAIT_SECONDS}s for the first loads to finish"
sleep "$WAIT_SECONDS"

upload orders_2026-10-02_bad.json orders_2026-10-02_bad.json
# identical content under a new name, to show duplicate protection
upload orders_2026-10-01.json orders_2026-10-01_resend.json
echo "==> Waiting ${WAIT_SECONDS}s for processing"
sleep "$WAIT_SECONDS"

query "Ingestion log" "
  SELECT status, source_file, rows_loaded, error_count, message
  FROM \`${PROJECT_ID}.${DATASET}.ingestion_log\`
  ORDER BY processed_at"

query "Orders" "
  SELECT order_id, customer_id, order_date, status, ARRAY_LENGTH(items) AS item_count,
         shipping.zip AS zip, ingested_at
  FROM \`${PROJECT_ID}.${DATASET}.orders\`
  ORDER BY order_id"

query "Customers" "
  SELECT customer_id, name, email, signup_date, is_member, country
  FROM \`${PROJECT_ID}.${DATASET}.customers\`
  ORDER BY customer_id"

echo
echo "==> Quarantine folder"
gcloud storage ls "gs://$BUCKET/quarantine/"
echo
echo "==> Error report"
gcloud storage cat "gs://$BUCKET/quarantine/orders_2026-10-02_bad.json.error.json"
