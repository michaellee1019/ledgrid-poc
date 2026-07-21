#!/bin/bash
# Fetch manually saved animation presets from the deployed controller.

set -euo pipefail

PI_HOST="${PI_HOST:-ledgridwall@ledgridwall.local}"
DEPLOY_DIR="${DEPLOY_DIR:-ledgrid-pod}"
LOCAL_DIR="${LOCAL_DIR:-.}"

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"

local_presets_dir="$LOCAL_DIR/presets/animations"
remote_presets_dir="~/$DEPLOY_DIR/presets/animations"
mkdir -p "$local_presets_dir"

echo "[INFO] Checking animation presets on $PI_HOST..."
ssh $SSH_OPTS "$PI_HOST" "test -d $remote_presets_dir"

echo "[INFO] Fetching new manually saved presets..."
fetch_output=$(rsync -az \
    --ignore-existing \
    --itemize-changes \
    --omit-dir-times \
    --exclude 'before-deploy.json' \
    -e "ssh $SSH_OPTS" \
    "$PI_HOST:$remote_presets_dir/" \
    "$local_presets_dir/")

if [ -n "$fetch_output" ]; then
    echo "$fetch_output"
    echo "[SUCCESS] New presets fetched into presets/animations/."
else
    echo "[INFO] No new manually saved presets were found."
fi

echo "[INFO] Fetched runtime presets remain ignored until explicitly curated."
echo "[INFO] Inspect with: git ls-files --others --ignored --exclude-standard 'presets/animations/**/*.json'"
echo "[INFO] Stage one with: git add -f presets/animations/<animation>/<preset>.json"
