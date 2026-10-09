#!/bin/bash
# Inspect current controller status and service logs using the dedicated key.
set -euo pipefail
source "$(dirname "$0")/../deployment/ssh_helpers.sh"
if [ -n "${OUT_FILE:-}" ]; then
    mkdir -p "$(dirname "$OUT_FILE")"
    exec >"$OUT_FILE" 2>&1
fi
if [ "${RESTART_WEB:-0}" = 1 ]; then
    ssh "${SSH_ARGS[@]}" "$PI_HOST" 'sudo -n systemctl restart ledgrid.service'
fi
ssh "${SSH_ARGS[@]}" "$PI_HOST" 'systemctl status ledgrid.service --no-pager; journalctl -u ledgrid.service -n 60 --no-pager; curl --max-time 5 -fsS http://127.0.0.1:5000/api/v1/composer/operations/telemetry'
