#!/usr/bin/env bash
# Deletes everything the lab created so it stops using credits.
# Usage, from the repository root: ./scripts/teardown.sh
set -euo pipefail
source "$(dirname "$0")/config.sh"

read -r -p "Delete function $FUNCTION_NAME, bucket gs://$BUCKET, and dataset $DATASET? [y/N] " answer
if [[ "$answer" != "y" ]]; then
  echo "Cancelled."
  exit 0
fi

gcloud functions delete "$FUNCTION_NAME" --gen2 --region="$REGION" --quiet || true
gcloud storage rm -r "gs://$BUCKET" || true
bq rm -r -f --dataset "${PROJECT_ID}:${DATASET}" || true
# container images built during deployment are stored here
gcloud artifacts repositories delete gcf-artifacts --location="$REGION" --quiet || true
echo "==> Teardown complete."
