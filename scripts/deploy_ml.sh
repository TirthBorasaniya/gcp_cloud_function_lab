#!/usr/bin/env bash
# Deploys the ML example as a public HTTP Cloud Function.
# Run ./scripts/deploy.sh first so the required APIs are enabled.
# Usage, from the repository root: ./scripts/deploy_ml.sh
set -euo pipefail
source "$(dirname "$0")/config.sh"

echo "==> Deploying function $ML_FUNCTION_NAME (takes 2 to 4 minutes)"
gcloud functions deploy "$ML_FUNCTION_NAME" \
  --gen2 \
  --runtime=python311 \
  --region="$REGION" \
  --source=ML-Example \
  --entry-point=predict \
  --trigger-http \
  --allow-unauthenticated \
  --memory=512Mi \
  --timeout=60s \
  --max-instances=2

echo "==> Done. Run ./scripts/run_ml_demo.sh to send test requests."
