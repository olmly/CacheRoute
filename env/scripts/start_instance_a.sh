#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT_DIR}/env/scripts/prepare_two_instances.sh" >/dev/null

export PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PROXY_CP_URL="${PROXY_CP_URL:-http://127.0.0.1:8002}"
export VLLM_BASE_URL="http://127.0.0.1:8000"
export USE_MOCK=false
export INSTANCE_ID="${INSTANCE_A_ID:-127.0.0.1:9001}"
export INSTANCE_VLLM_HEALTH_URL="http://127.0.0.1:8000/health"
export INSTANCE_VLLM_FAILURE_THRESHOLD="${INSTANCE_VLLM_FAILURE_THRESHOLD:-3}"
export INSTANCE_BOOT_ID="${INSTANCE_A_BOOT_ID}"
export INSTANCE_CP_HOST=127.0.0.1
export INSTANCE_CP_PORT=9002

cd "${ROOT_DIR}"
exec python3 test/demo_instance.py --host 127.0.0.1 --port 9001 --proxy-cp-url "${PROXY_CP_URL}" --no-resource-monitor --no-ui
