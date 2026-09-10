#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null

export CUDA_VISIBLE_DEVICES="${VLLM_A_GPUS:-0,1,2,3,4,5,6,7}"
export LMCACHE_CONFIG_FILE="${ROOT_DIR}/_vllm_lmcache/lmcache_instance_a.yaml"

exec lmcache server --host 127.0.0.1 --port 5555 --http-port 8080 --l1-size-gb 20 --eviction-policy LRU --chunk-size 64
