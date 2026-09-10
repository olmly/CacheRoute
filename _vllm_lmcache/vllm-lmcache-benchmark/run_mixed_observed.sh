#!/usr/bin/env bash
set -euo pipefail

# Realistic 70/20/10 mix: short chat, long shared-context RAG, and long agent output.
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
MODEL="${MODEL:?Set MODEL, for example: export MODEL=llama-3-70b-instruct}"
REQUESTS="${REQUESTS:-2}"
CONCURRENCY="${CONCURRENCY:-1}"
PREFIX_REPEATS="${PREFIX_REPEATS:-96}"
WORKLOAD="results/workloads/mixed-${REQUESTS}.jsonl"

python3 generate_workloads.py --scenario mixed --requests "$REQUESTS" \
  --shared-prefix-repeats "$PREFIX_REPEATS" --output "$WORKLOAD"
./capture_metrics.sh before-mixed
python3 benchmark_chat.py --base-url "$BASE_URL" --model "$MODEL" \
  --workload-file "$WORKLOAD" --requests "$REQUESTS" --concurrency "$CONCURRENCY" \
  --output-dir results/mixed
./capture_metrics.sh after-mixed
summary="$(ls -t results/mixed/summary-*.json | head -n 1)"
python3 summarize_metrics.py --before results/metrics/latest-before-mixed.prom \
  --after results/metrics/latest-after-mixed.prom --client-summary "$summary"
