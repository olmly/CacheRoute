#!/usr/bin/env bash
set -euo pipefail

RUN_DIR="${CACHEROUTE_AB_RUN_DIR:-/tmp/cacheroute-stack-ab}"
PID_DIR="${RUN_DIR}/pids"
FORCE_STOP="${CACHEROUTE_FORCE_STOP:-0}"
SUPERVISOR_PID_FILE="${PID_DIR}/supervisor.pid"

pid_is_live() {
  local pid="$1"
  local state
  [[ -r "/proc/${pid}/stat" ]] || return 1
  state="$(awk '{print $3}' "/proc/${pid}/stat")"
  [[ "${state}" != "Z" ]]
}

stop_service() {
  local name="$1"
  local expected="$2"
  local pid_file="${PID_DIR}/${name}.pid"

  if [[ ! -f "${pid_file}" ]]; then
    printf '[CacheRoute] %-12s no PID file\n' "${name}"
    return
  fi

  local pid
  pid="$(<"${pid_file}")"
  if ! pid_is_live "${pid}"; then
    printf '[CacheRoute] %-12s already stopped pid=%s\n' "${name}" "${pid}"
    rm -f "${pid_file}"
    return
  fi

  local cmdline
  cmdline="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
  if [[ "${cmdline}" != *"${expected}"* ]]; then
    printf '[CacheRoute] %-12s refused pid=%s: unexpected command=%s\n' "${name}" "${pid}" "${cmdline}"
    return
  fi

  kill -TERM "${pid}"
  for _ in $(seq 1 20); do
    if ! pid_is_live "${pid}"; then
      rm -f "${pid_file}"
      printf '[CacheRoute] %-12s stopped pid=%s\n' "${name}" "${pid}"
      return
    fi
    sleep 0.5
  done

  if [[ "${FORCE_STOP}" == "1" ]]; then
    kill -KILL "${pid}" 2>/dev/null || true
    rm -f "${pid_file}"
    printf '[CacheRoute] %-12s force-stopped pid=%s\n' "${name}" "${pid}"
  else
    printf '[CacheRoute] %-12s still stopping pid=%s; set CACHEROUTE_FORCE_STOP=1 to force it\n' "${name}" "${pid}"
  fi
}

if [[ -f "${SUPERVISOR_PID_FILE}" ]] && pid_is_live "$(<"${SUPERVISOR_PID_FILE}")"; then
  supervisor_pid="$(<"${SUPERVISOR_PID_FILE}")"
  kill -TERM "${supervisor_pid}"
  for _ in $(seq 1 20); do
    pid_is_live "${supervisor_pid}" || break
    sleep 0.5
  done
fi
rm -f "${SUPERVISOR_PID_FILE}"

# Stop dependents before the services they use.
stop_service instance-b "test/demo_instance.py"
stop_service instance-a "test/demo_instance.py"
stop_service vllm-b "vllm.entrypoints.openai.api_server"
stop_service vllm-a "vllm.entrypoints.openai.api_server"
stop_service lmcache-b "lmcache server"
stop_service lmcache-a "lmcache server"
stop_service proxy "test/demo_proxy.py"
