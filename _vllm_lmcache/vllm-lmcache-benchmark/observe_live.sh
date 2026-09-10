#!/usr/bin/env bash
set -euo pipefail

# Poll vLLM's live scheduler state while a benchmark runs in another terminal.
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
INTERVAL="${INTERVAL:-0.5}"
mkdir -p results/live
output="results/live/vllm-live-$(date -u +%Y%m%dT%H%M%SZ).csv"

echo "timestamp,running,waiting,kv_cache_usage_percent,prompt_tokens_total,generation_tokens_total,preemptions_total" | tee "$output"
echo "Press Ctrl-C to stop. Saving to $output" >&2

while true; do
  metrics="$(curl --fail --silent --show-error "$BASE_URL/metrics")"
  values="$(printf '%s\n' "$metrics" | awk '
    /^vllm:num_requests_running\{/ { running += $NF }
    /^vllm:num_requests_waiting\{/ { waiting += $NF }
    /^vllm:kv_cache_usage_perc\{/ { kv += $NF }
    /^vllm:prompt_tokens_total\{/ { prompt += $NF }
    /^vllm:generation_tokens_total\{/ { generated += $NF }
    /^vllm:num_preemptions_total\{/ { preemptions += $NF }
    END { printf "%.0f,%.0f,%.2f,%.0f,%.0f,%.0f", running, waiting, kv * 100, prompt, generated, preemptions }
  ')"
  printf '%s,%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$values" | tee -a "$output"
  sleep "$INTERVAL"
done
