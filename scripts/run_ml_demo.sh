#!/usr/bin/env bash
# Sends a single, a batch, and an invalid request to the deployed ML function.
# Usage, from the repository root: ./scripts/run_ml_demo.sh
set -euo pipefail
source "$(dirname "$0")/config.sh"

URL=$(gcloud functions describe "$ML_FUNCTION_NAME" --gen2 --region="$REGION" \
  --format="value(serviceConfig.uri)")
echo "==> Function URL: $URL"

send() {
  echo
  echo "==> $1"
  echo "Request: $2"
  curl -s -X POST "$URL" -H "Content-Type: application/json" -d "$2" \
    -w "\nHTTP status: %{http_code}\n"
}

send "Single prediction" '{"features": [5.1, 3.5, 1.4, 0.2]}'
send "Batch prediction" '{"instances": [[5.1, 3.5, 1.4, 0.2], [6.0, 2.9, 4.5, 1.5], [6.7, 3.0, 5.2, 2.3]]}'
send "Invalid request (string value)" '{"features": ["5.1", 3.5, 1.4, 0.2]}'
