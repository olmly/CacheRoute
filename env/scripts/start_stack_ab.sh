#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="${CACHEROUTE_AB_RUN_DIR:-/tmp/cacheroute-stack-ab}"
LOG_DIR="${RUN_DIR}/logs"
PID_DIR="${RUN_DIR}/pids"
SUPERVISOR_PID_FILE="${PID_DIR}/supervisor.pid"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

# Consume a requested renewal before spawning the long-lived supervisor.
# shellcheck source=/dev/null
source "${ROOT_DIR}/env/scripts/prepare_two_instances.sh"

pid_is_live() {
  local pid="$1"
  local state
  [[ -r "/proc/${pid}/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/${pid}/stat")"
  [[ "${state}" != "Z" ]]
}

if [[ -f "${SUPERVISOR_PID_FILE}" ]] && pid_is_live "$(<"${SUPERVISOR_PID_FILE}")"; then
  printf '[CacheRoute] supervisor already running pid=%s\n' "$(<"${SUPERVISOR_PID_FILE}")"
  exit 0
fi

rm -f "${SUPERVISOR_PID_FILE}"
nohup bash "${ROOT_DIR}/env/scripts/run_stack_ab.sh" >"${LOG_DIR}/supervisor.log" 2>&1 &
supervisor_pid=$!
printf '%s\n' "${supervisor_pid}" >"${SUPERVISOR_PID_FILE}"
printf '[CacheRoute] supervisor started pid=%s log=%s\n' "${supervisor_pid}" "${LOG_DIR}/supervisor.log"

cat <<EOF

[CacheRoute] all launch commands were submitted.
  status: bash ${ROOT_DIR}/env/scripts/status_stack_ab.sh
  logs:   tail -f ${LOG_DIR}/supervisor.log or ${LOG_DIR}/<service>.log
  proxy:  http://127.0.0.1:8002/v1/instance/list
EOF
