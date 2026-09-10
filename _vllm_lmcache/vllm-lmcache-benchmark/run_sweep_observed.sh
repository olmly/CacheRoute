#!/usr/bin/env bash
set -euo pipefail

# Captures and summarizes each concurrency level independently.
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
MODEL="${MODEL:?Set MODEL, for example: export MODEL=llama-3-70b-instruct}"
PROMPT_FILE="${PROMPT_FILE:-prompts.txt}"
MAX_TOKENS="${MAX_TOKENS:-64}"
CONCURRENCY_LEVELS="${CONCURRENCY_LEVELS:-1 2 4 8 16 32}"

[[ -f "$PROMPT_FILE" ]] || { echo "提示词文件不存在: $PROMPT_FILE" >&2; exit 1; }
request_args=()
if [[ -n "${REQUESTS_PER_LEVEL:-}" ]]; then
  request_args=(--requests "$REQUESTS_PER_LEVEL")
fi
for concurrency in $CONCURRENCY_LEVELS; do
  label="c${concurrency}"
  echo "=== 配置: prompt_file=${PROMPT_FILE}, requests=${REQUESTS_PER_LEVEL:-全部提示词}, concurrency=${concurrency}, max_tokens=${MAX_TOKENS} ==="
  ./capture_metrics.sh "before-${label}"
  python3 benchmark_chat.py \
    --base-url "$BASE_URL" \
    --model "$MODEL" \
    --prompt-file "$PROMPT_FILE" \
    --max-tokens "$MAX_TOKENS" \
    --concurrency "$concurrency" \
    "${request_args[@]}" \
    --output-dir "results/sweep-c${concurrency}"
  ./capture_metrics.sh "after-${label}"
  summary="$(ls -t "results/sweep-c${concurrency}"/summary-*.json | head -n 1)"
  python3 summarize_metrics.py \
    --before "results/metrics/latest-before-${label}.prom" \
    --after "results/metrics/latest-after-${label}.prom" \
    --client-summary "$summary"
done
