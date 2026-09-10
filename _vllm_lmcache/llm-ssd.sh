#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES="${VLLM_A_GPUS:-0,1,2,3,4,5,6,7}"
export LMCACHE_LOG_LEVEL=DEBUG

DISK_CACHE_DIR="/workspace/llm-stack/.lmcache-ssd-a"
mkdir -p "$DISK_CACHE_DIR"

exec lmcache server \
  --host 127.0.0.1 \
  --port 5555 \
  --http-port 8080 \
  --chunk-size 64 \
  --l1-size-gb 2 \
  --eviction-policy noop \
  --l2-store-policy skip_l1 \
  --l2-adapter "{\"type\":\"fs\",\"base_path\":\"${DISK_CACHE_DIR}\",\"use_odirect\":true}"