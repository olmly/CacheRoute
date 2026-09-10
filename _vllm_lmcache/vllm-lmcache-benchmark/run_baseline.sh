#!/usr/bin/env bash
set -euo pipefail

# Reads prompts.txt from top to bottom. One request is sent for each prompt by default.
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
MODEL="${MODEL:?Set MODEL, for example: export MODEL=llama-3-70b-instruct}"
PROMPT_FILE="${PROMPT_FILE:-prompts.txt}"
MAX_TOKENS="${MAX_TOKENS:-64}"
CONCURRENCY="${CONCURRENCY:-1}"

[[ -f "$PROMPT_FILE" ]] || { echo "提示词文件不存在: $PROMPT_FILE" >&2; exit 1; }
request_args=()
if [[ -n "${REQUESTS:-}" ]]; then
  request_args=(--requests "$REQUESTS")
fi
echo "配置: prompt_file=${PROMPT_FILE}, requests=${REQUESTS:-全部提示词}, concurrency=${CONCURRENCY}, max_tokens=${MAX_TOKENS}"

python3 benchmark_chat.py \
  --base-url "$BASE_URL" \
  --model "$MODEL" \
  --prompt-file "$PROMPT_FILE" \
  --max-tokens "$MAX_TOKENS" \
  --concurrency "$CONCURRENCY" \
  "${request_args[@]}" \
  --output-dir results/baseline
