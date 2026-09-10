#!/usr/bin/env bash
set -euo pipefail

export CUDA_VISIBLE_DEVICES="${VLLM_A_GPUS:-2,3}"
MODEL_DIR="${MODEL_DIR:-/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B}"

exec python3 -m vllm.entrypoints.openai.api_server \
  --model "${MODEL_DIR}" \
  --served-model-name deepseek-r1-distill-qwen-7b-a \
  --host 127.0.0.1 \
  --port 8000 \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.85 \
  --max-model-len 1024 \
  --max-num-seqs 1 \
  --enforce-eager \
  --disable-hybrid-kv-cache-manager \
  --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"LMCacheMPConnector","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_role":"kv_both","kv_connector_extra_config":{"lmcache.mp.host":"tcp://127.0.0.1","lmcache.mp.port":5555}}'
