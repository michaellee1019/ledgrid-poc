#!/bin/bash
# Fast application-code deployment. Full provisioning remains in deploy.sh.

set -euo pipefail

PI_HOST="${PI_HOST:-ledgridwall@ledgridwall.local}"
DEPLOY_DIR="${DEPLOY_DIR:-ledgrid-pod}"
LOCAL_DIR="${LOCAL_DIR:-.}"

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"

echo "[INFO] Checking $PI_HOST..."
ssh $SSH_OPTS "$PI_HOST" "test -x ~/$DEPLOY_DIR/venv/bin/python && sudo -n true"

echo "[INFO] Syncing application code and web templates..."
rsync -az \
    -e "ssh $SSH_OPTS" \
    --exclude '.git/' \
    --exclude 'venv/' \
    --exclude '.venv*/' \
    --exclude 'test_venv/' \
    --exclude 'whos-turn-tracker/' \
    --exclude '__pycache__/' \
    --exclude '.pio/' \
    --exclude 'build/' \
    --exclude 'dist/' \
    --include '*/' \
    --include '*.py' \
    --include '*.html' \
    --include 'config/webcam_pixel_map.json' \
    --exclude '*' \
    "$LOCAL_DIR"/ "$PI_HOST:~/$DEPLOY_DIR/"

# Deploy only curated preset JSON that Git considers trackable. Runtime presets
# are ignored by Git and therefore remain local to whichever host created them.
echo "[INFO] Syncing curated animation presets..."
git -C "$LOCAL_DIR" ls-files --cached --others --exclude-standard -z \
    -- 'presets/animations/**/*.json' \
    | rsync -az --from0 --files-from=- \
        -e "ssh $SSH_OPTS" \
        "$LOCAL_DIR"/ "$PI_HOST:~/$DEPLOY_DIR/"

echo "[INFO] Saving active settings as the before-deploy preset..."
ssh $SSH_OPTS "$PI_HOST" \
    "cd ~/$DEPLOY_DIR && venv/bin/python tools/deployment/preserve_deploy_settings.py save"

echo "[INFO] Restarting web server and controller..."
ssh $SSH_OPTS "$PI_HOST" "sudo systemctl restart ledgrid.service"

echo "[INFO] Restoring the before-deploy preset..."
ssh $SSH_OPTS "$PI_HOST" \
    "cd ~/$DEPLOY_DIR && venv/bin/python tools/deployment/preserve_deploy_settings.py restore --wait 20"

echo "[INFO] Recording deployment timestamp..."
ssh $SSH_OPTS "$PI_HOST" \
    "cd ~/$DEPLOY_DIR && venv/bin/python tools/deployment/preserve_deploy_settings.py record-deploy"

echo "[INFO] Checking web server..."
ssh $SSH_OPTS "$PI_HOST" "for attempt in {1..40}; do curl --fail --silent --max-time 2 http://127.0.0.1:5000/api/status >/dev/null && exit 0; sleep 0.25; done; exit 1"

echo "[SUCCESS] Application deployment complete; previous settings restored."
