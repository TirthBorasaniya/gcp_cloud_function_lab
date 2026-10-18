#!/usr/bin/env bash
# Creates the bucket, dataset, and IAM roles, then deploys the Cloud Function.
# Safe to run again: existing resources are left as they are.
# Usage, from the repository root: ./scripts/deploy.sh
set -euo pipefail
source "$(dirname "$0")/config.sh"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo "==> Project $PROJECT_ID, region $REGION, bucket gs://$BUCKET, dataset $DATASET"

echo "==> Enabling APIs (first run takes a minute)"
gcloud services enable \
  cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com eventarc.googleapis.com pubsub.googleapis.com \
  storage.googleapis.com bigquery.googleapis.com logging.googleapis.com

echo "==> Creating bucket"
if ! gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access
fi

echo "==> Creating BigQuery dataset"
if ! bq show --dataset "${PROJECT_ID}:${DATASET}" >/dev/null 2>&1; then
  bq --location="$BQ_LOCATION" mk --dataset "${PROJECT_ID}:${DATASET}"
fi

echo "==> Granting IAM roles"
grant_project_role() {
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$1" --role="$2" --condition=None --quiet >/dev/null
}
# least-privilege roles instead of the original lab's BigQuery Admin
grant_project_role "$COMPUTE_SA" roles/bigquery.dataEditor
grant_project_role "$COMPUTE_SA" roles/bigquery.jobUser
grant_project_role "$COMPUTE_SA" roles/eventarc.eventReceiver
grant_project_role "$COMPUTE_SA" roles/run.invoker
grant_project_role "$COMPUTE_SA" roles/cloudbuild.builds.builder
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$COMPUTE_SA" --role=roles/storage.objectAdmin >/dev/null
# Cloud Storage publishes upload events through Pub/Sub using its own service agent
GCS_SA=$(gcloud storage service-agent --project="$PROJECT_ID")
grant_project_role "$GCS_SA" roles/pubsub.publisher

echo "==> Deploying function $FUNCTION_NAME (takes 2 to 4 minutes)"
gcloud functions deploy "$FUNCTION_NAME" \
  --gen2 \
  --runtime=python311 \
  --region="$REGION" \
  --source=. \
  --entry-point=process_upload \
  --trigger-event-filters="type=google.cloud.storage.object.v1.finalized" \
  --trigger-event-filters="bucket=$BUCKET" \
  --set-env-vars="BQ_DATASET=$DATASET" \
  --memory=512Mi \
  --timeout=120s \
  --max-instances=3

echo "==> Done. Run ./scripts/run_demo.sh to upload the sample files."
