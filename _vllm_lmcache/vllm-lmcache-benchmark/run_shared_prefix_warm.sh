#!/usr/bin/env bash
set -euo pipefail

# Populate LMCache with one long prefix, then measure requests with that exact prefix.
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
MODEL="${MODEL:?Set MODEL, for example: export MODEL=llama-3-70b-instruct}"
REQUESTS="${REQUESTS:-32}"
CONCURRENCY="${CONCURRENCY:-8}"
PREFIX_REPEATS="${PREFIX_REPEATS:-96}"
CACHE_SETTLE_SECONDS="${CACHE_SETTLE_SECONDS:-3}"
WORKLOAD="results/workloads/shared-prefix-${REQUESTS}.jsonl"

python3 generate_workloads.py --scenario shared-prefix --requests "$REQUESTS" \
  --shared-prefix-repeats "$PREFIX_REPEATS" --output "$WORKLOAD"

echo "Warming LMCache with one request..."
python3 benchmark_chat.py --base-url "$BASE_URL" --model "$MODEL" \
  --workload-file "$WORKLOAD" --requests 1 --concurrency 1 --output-dir results/shared-prefix-warmup
sleep "$CACHE_SETTLE_SECONDS"

./capture_metrics.sh before-shared-prefix
python3 benchmark_chat.py --base-url "$BASE_URL" --model "$MODEL" \
  --workload-file "$WORKLOAD" --requests "$REQUESTS" --concurrency "$CONCURRENCY" \
  --output-dir results/shared-prefix-warm
./capture_metrics.sh after-shared-prefix
summary="$(ls -t results/shared-prefix-warm/summary-*.json | head -n 1)"
python3 summarize_metrics.py --before results/metrics/latest-before-shared-prefix.prom \
  --after results/metrics/latest-after-shared-prefix.prom --client-summary "$summary"
