#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"
export PROXY_CACHE_ZMQ_ENDPOINTS="${PROXY_CACHE_ZMQ_ENDPOINTS:-tcp://127.0.0.1:5557,tcp://127.0.0.1:5558}"

cd "${ROOT_DIR}"
exec python3 test/demo_proxy.py --host 127.0.0.1 --port 8001 "$@"
