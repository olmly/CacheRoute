#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES="${VLLM_A_GPUS:-0,1,2,3,4,5,6,7}"
MODEL_DIR="${MODEL_DIR:-/workspace/llm-stack/models/LLM-Research/Meta-Llama-3-70B-Instruct}"

exec python3 -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_DIR}" \
  --served-model-name llama-3-70b-instruct \
  --host 127.0.0.1 \
  --port 8000 \
  --tensor-parallel-size 8 \
  --gpu-memory-utilization 0.85 \
  --max-model-len 8192 \
  --no-enable-prefix-caching \
  --max-num-seqs 16 \
  --disable-hybrid-kv-cache-manager \
  --kv-transfer-config '{"kv_connector":"LMCacheMPConnector","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"tcp://127.0.0.1","lmcache.mp.port":5555}}'