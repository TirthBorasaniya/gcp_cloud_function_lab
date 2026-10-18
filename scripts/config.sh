#!/usr/bin/env bash
# Shared settings for the deploy, demo, and teardown scripts. Override any value with an environment variable.

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
BUCKET="${BUCKET:-${PROJECT_ID}-etl-uploads}"
DATASET="${DATASET:-ecommerce}"
BQ_LOCATION="${BQ_LOCATION:-US}"
FUNCTION_NAME="${FUNCTION_NAME:-etl-loader}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "No GCP project selected. Run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi
