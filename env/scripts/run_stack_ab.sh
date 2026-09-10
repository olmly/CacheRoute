#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="${CACHEROUTE_AB_RUN_DIR:-/tmp/cacheroute-stack-ab}"
LOG_DIR="${RUN_DIR}/logs"
PID_DIR="${RUN_DIR}/pids"
SERVICE_PIDS=()

mkdir -p "${LOG_DIR}" "${PID_DIR}"
# shellcheck source=/dev/null
source "${ROOT_DIR}/env/scripts/prepare_two_instances.sh"

pid_is_live() {
  local pid="$1" state
  [[ -r "/proc/${pid}/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/${pid}/stat")"
  [[ "${state}" != "Z" ]]
}

start_service() {
  local name="$1" script="$2"
  local pid_file="${PID_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"
  if [[ -f "${pid_file}" ]] && pid_is_live "$(<"${pid_file}")"; then
    printf '[CacheRoute] %-12s already running pid=%s\n' "${name}" "$(<"${pid_file}")"
    return
  fi
  rm -f "${pid_file}"
  bash "${ROOT_DIR}/env/scripts/${script}" >"${log_file}" 2>&1 &
  local pid=$!
  SERVICE_PIDS+=("${pid}")
  printf '%s\n' "${pid}" >"${pid_file}"
  printf '[CacheRoute] %-12s started pid=%s log=%s\n' "${name}" "${pid}" "${log_file}"
}

shutdown_children() {
  printf '[CacheRoute] supervisor stopping child services\n'
  for pid in "${SERVICE_PIDS[@]}"; do
    pid_is_live "${pid}" && kill -TERM "${pid}" 2>/dev/null || true
  done
  for pid in "${SERVICE_PIDS[@]}"; do
    wait "${pid}" 2>/dev/null || true
  done
  exit 0
}
trap shutdown_children INT TERM

start_service proxy start_proxy_ab.sh
sleep 1
start_service lmcache-a start_lmcache_a.sh
start_service lmcache-b start_lmcache_b.sh
sleep 1
start_service vllm-a start_vllm_a.sh
start_service vllm-b start_vllm_b.sh
sleep 1
start_service instance-a start_instance_a.sh
start_service instance-b start_instance_b.sh

# Reap each completed child. This is the essential difference from a launcher
# that exits immediately after putting services in the background.
while ((${#SERVICE_PIDS[@]})); do
  remaining=()
  for pid in "${SERVICE_PIDS[@]}"; do
    if pid_is_live "${pid}"; then
      remaining+=("${pid}")
    else
      wait "${pid}" 2>/dev/null || true
      printf '[CacheRoute] child exited pid=%s\n' "${pid}"
    fi
  done
  SERVICE_PIDS=("${remaining[@]}")
  ((${#SERVICE_PIDS[@]})) && sleep 1
done
