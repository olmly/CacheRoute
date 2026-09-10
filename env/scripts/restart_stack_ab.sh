#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

bash "${ROOT_DIR}/env/scripts/stop_stack_ab.sh"

# A full restart represents new vLLM/LMCache process generations.
export CACHEROUTE_RENEW_BOOT_IDS=1
bash "${ROOT_DIR}/env/scripts/start_stack_ab.sh"
unset CACHEROUTE_RENEW_BOOT_IDS
