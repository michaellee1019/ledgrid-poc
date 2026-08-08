#!/bin/bash
# Fast application-code deployment. Full provisioning remains in deploy.sh.

set -euo pipefail

PI_HOST="${PI_HOST:-ledgridwall@ledgridwall.local}"
DEPLOY_DIR="${DEPLOY_DIR:-ledgrid-pod}"
LOCAL_DIR="${LOCAL_DIR:-.}"

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"
# shellcheck source=sync_files.sh
source "$(dirname "$0")/sync_files.sh"

echo "[INFO] Checking $PI_HOST..."
ssh $SSH_OPTS "$PI_HOST" "test -x ~/$DEPLOY_DIR/venv/bin/python && sudo -n true"

echo "[INFO] Syncing tracked application code, web templates, and plugin assets..."
sync_fast_deployment

restore_saved=0
if ssh $SSH_OPTS "$PI_HOST" \
    "curl --fail --silent --max-time 2 http://127.0.0.1:5000/api/status >/dev/null"; then
    echo "[INFO] Saving active settings as the before-deploy preset..."
    ssh $SSH_OPTS "$PI_HOST" \
        "cd ~/$DEPLOY_DIR && venv/bin/python tools/deployment/preserve_deploy_settings.py save"
    restore_saved=1
else
    echo "[WARNING] Existing web service is unhealthy; skipping pre-deploy state capture"
fi

echo "[INFO] Restarting web server and controller..."
ssh $SSH_OPTS "$PI_HOST" "sudo systemctl restart ledgrid.service"

if [ "$restore_saved" = 1 ]; then
    echo "[INFO] Restoring the before-deploy preset..."
    ssh $SSH_OPTS "$PI_HOST" \
        "cd ~/$DEPLOY_DIR && venv/bin/python tools/deployment/preserve_deploy_settings.py restore --wait 20"
fi

echo "[INFO] Recording deployment timestamp..."
ssh $SSH_OPTS "$PI_HOST" \
    "cd ~/$DEPLOY_DIR && venv/bin/python tools/deployment/preserve_deploy_settings.py record-deploy"

echo "[INFO] Checking web server..."
if ! ssh $SSH_OPTS "$PI_HOST" "for attempt in {1..120}; do curl --fail --silent --max-time 2 http://127.0.0.1:5000/api/status >/dev/null && exit 0; sleep 0.25; done; exit 1"; then
    echo "[ERROR] Web server did not become healthy; collecting startup logs" >&2
    ssh $SSH_OPTS "$PI_HOST" \
        "sudo systemctl status ledgrid.service --no-pager -l || true; tail -80 ~/$DEPLOY_DIR/web.log 2>/dev/null || true; tail -80 ~/$DEPLOY_DIR/controller.log 2>/dev/null || true"
    exit 1
fi

echo "[SUCCESS] Application deployment complete; previous settings restored."
