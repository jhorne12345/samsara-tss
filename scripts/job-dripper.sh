#!/usr/bin/env bash
# Continuously submit jobs to a running dispatcher so the chaos demo has
# something to chew on. Killed by `make demo-stop`.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TSS="${REPO_ROOT}/.venv/bin/tss"
DISPATCHER="${TSS_DISPATCHER:-http://127.0.0.1:8080}"
PRODUCTS=(vehicle_gateway asset_gateway)

while true; do
  product="${PRODUCTS[$((RANDOM % 2))]}"
  duration=$((4 + RANDOM % 8))
  "$TSS" submit-job \
    --product "$product" \
    --duration "$duration" \
    --submitter chaos-demo \
    --dispatcher "$DISPATCHER" >/dev/null 2>&1 || true
  sleep $((2 + RANDOM % 3))
done
