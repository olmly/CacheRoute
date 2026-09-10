#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${CACHEROUTE_AB_RUN_DIR:-/tmp/cacheroute-stack-ab}"
PID_DIR="${RUN_DIR}/pids"
SUPERVISOR_PID_FILE="${PID_DIR}/supervisor.pid"

pid_is_live() {
  local pid="$1"
  local state
  [[ -r "/proc/${pid}/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/${pid}/stat")"
  [[ "${state}" != "Z" ]]
}

if [[ -f "${SUPERVISOR_PID_FILE}" ]] && pid_is_live "$(<"${SUPERVISOR_PID_FILE}")"; then
  printf '%-12s running pid=%s\n' supervisor "$(<"${SUPERVISOR_PID_FILE}")"
else
  printf '%-12s stopped\n' supervisor
fi

for name in proxy lmcache-a lmcache-b vllm-a vllm-b instance-a instance-b; do
  pid_file="${PID_DIR}/${name}.pid"
  if [[ -f "${pid_file}" ]] && pid_is_live "$(<"${pid_file}")"; then
    printf '%-12s running pid=%s\n' "${name}" "$(<"${pid_file}")"
  else
    printf '%-12s stopped\n' "${name}"
  fi
done
