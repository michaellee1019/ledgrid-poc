#!/bin/bash
# Deploy-helper script that leverages the running web API and SSH access to
# capture diagnostic information when strips go dark.

set -euo pipefail

PI_HOST="${PI_HOST:-ledwallleft@ledwallleft.local}"
REMOTE_DIR="${REMOTE_DIR:-ledgrid-pod}"
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new}"
API_PORT="${API_PORT:-5000}"
DIAG_PATTERN="${DIAG_PATTERN:-strip_chase}"
DIAG_INTENSITY="${DIAG_INTENSITY:-200}"
DIAG_WAIT="${DIAG_WAIT:-15}"
STATUS_POLLS="${STATUS_POLLS:-3}"

timestamp() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

log() {
    echo "[$(timestamp)] $*"
}

run_ssh() {
    ssh $SSH_OPTS "$PI_HOST" "$@"
}

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

require_cmd curl

log "Collecting remote diagnostics (host=$PI_HOST, dir=$REMOTE_DIR)"

PI_IP=$(run_ssh "hostname -I | awk '{print \$1}' | tr -d '\r'")
if [ -z "$PI_IP" ]; then
    echo "Failed to resolve remote IP address" >&2
    exit 1
fi

API_BASE="http://$PI_IP:$API_PORT"
log "Resolved remote IP: $PI_IP (API base: $API_BASE)"

payload=$(printf '{"pattern":"%s","intensity":%s,"log_interval":1.0}' "$DIAG_PATTERN" "$DIAG_INTENSITY")
log "Triggering hardware diagnostics via API (pattern=$DIAG_PATTERN, intensity=$DIAG_INTENSITY)"
curl -sS -X POST "$API_BASE/api/start/hardware_diagnostics" \
    -H "Content-Type: application/json" \
    -d "$payload"
echo

log "Waiting ${DIAG_WAIT}s for diagnostic animation to stabilize..."
sleep "$DIAG_WAIT"

for i in $(seq 1 "$STATUS_POLLS"); do
    log "Fetching /api/status snapshot #$i"
    curl -sS "$API_BASE/api/status"
    echo
    sleep 1
done

log "Fetching /api/stats payload"
curl -sS "$API_BASE/api/stats"
echo

log "Fetching /api/frame payload"
curl -sS "$API_BASE/api/frame"
echo

remote_cmd() {
    run_ssh "cd ~/$REMOTE_DIR && $1"
}

log "--- Remote uptime ---"
run_ssh "uptime"

log "--- Remote disk usage ---"
run_ssh "df -h /"

log "--- run_state directory listing ---"
remote_cmd "ls -al run_state"

log "--- run_state/status.json ---"
remote_cmd "if [ -f run_state/status.json ]; then cat run_state/status.json; else echo 'status.json missing'; fi"

log "--- run_state/control.json ---"
remote_cmd "if [ -f run_state/control.json ]; then cat run_state/control.json; else echo 'control.json missing'; fi"

log "--- controller.log (last 200 lines) ---"
remote_cmd "if [ -f controller.log ]; then tail -n 200 controller.log; else echo 'controller.log missing'; fi"

log "--- web.log (last 120 lines) ---"
remote_cmd "if [ -f web.log ]; then tail -n 120 web.log; else echo 'web.log missing'; fi"

log "--- animation_system.log (last 80 lines) ---"
remote_cmd "if [ -f animation_system.log ]; then tail -n 80 animation_system.log; else echo 'animation_system.log missing'; fi"

log "--- Python status dump (hardware diagnostics runtime stats) ---"
remote_cmd "if [ -f venv/bin/activate ]; then source venv/bin/activate; fi; \
python3 scripts/dump_animation_snapshot.py"

log "Remote diagnostics capture complete."
