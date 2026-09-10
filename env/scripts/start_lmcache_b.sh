#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "${ROOT_DIR}/env/scripts/prepare_two_instances.sh" >/dev/null

export CUDA_VISIBLE_DEVICES="${VLLM_B_GPUS:-4,5}"
export LMCACHE_CONFIG_FILE="${ROOT_DIR}/env/config/lmcache_instance_b.yaml"
export CACHEROUTE_INSTANCE_ID="${INSTANCE_B_ID:-127.0.0.1:9003}"
export CACHEROUTE_INSTANCE_BOOT_ID="${INSTANCE_B_BOOT_ID}"

exec lmcache server --host 127.0.0.1 --port 5556 --http-port 8081 --l1-size-gb 20 --eviction-policy LRU --chunk-size 64
