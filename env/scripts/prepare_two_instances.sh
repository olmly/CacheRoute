#!/usr/bin/env bash
set -euo pipefail

STATE_FILE="${CACHEROUTE_TWO_INSTANCE_STATE_FILE:-/tmp/cacheroute-two-instances.env}"

new_boot_id() {
  cat /proc/sys/kernel/random/uuid
}

if [[ "${CACHEROUTE_RENEW_BOOT_IDS:-0}" == "1" || ! -f "${STATE_FILE}" ]]; then
  umask 077
  printf 'export INSTANCE_A_BOOT_ID=%q\nexport INSTANCE_B_BOOT_ID=%q\n' \
    "$(new_boot_id)" "$(new_boot_id)" > "${STATE_FILE}"
fi

# Renewal is a one-shot operation. Child launchers must inherit the generated
# IDs, not the request to generate a new ID again.
unset CACHEROUTE_RENEW_BOOT_IDS

# shellcheck source=/dev/null
source "${STATE_FILE}"
export INSTANCE_A_BOOT_ID INSTANCE_B_BOOT_ID
printf '[CacheRoute] A boot_id=%s\n[CacheRoute] B boot_id=%s\n' "$INSTANCE_A_BOOT_ID" "$INSTANCE_B_BOOT_ID"
