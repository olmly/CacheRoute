#!/usr/bin/env bash
set -euo pipefail

# Capture endpoint metrics once before and once after a benchmark run.
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
LABEL="${1:-snapshot}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p results/metrics

curl --fail --silent --show-error "$BASE_URL/metrics" \
  -o "results/metrics/${LABEL}-${STAMP}.prom"
cp "results/metrics/${LABEL}-${STAMP}.prom" "results/metrics/latest-${LABEL}.prom"
echo "Saved results/metrics/${LABEL}-${STAMP}.prom"
