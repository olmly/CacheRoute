#!/usr/bin/env bash
set -euo pipefail

# Runs one baseline and prints only the delta that matters for analysis.
./capture_metrics.sh before-baseline
./run_baseline.sh
./capture_metrics.sh after-baseline

summary="$(ls -t results/baseline/summary-*.json | head -n 1)"
python3 summarize_metrics.py \
  --before results/metrics/latest-before-baseline.prom \
  --after results/metrics/latest-after-baseline.prom \
  --client-summary "$summary"
