#!/bin/bash
# Refresh wall-authored plant masks and fetch new runtime animation presets.

set -euo pipefail

PI_HOST="${PI_HOST:-ledgridwall@ledgridwall.local}"
DEPLOY_DIR="${DEPLOY_DIR:-ledgrid-pod}"
LOCAL_DIR="${LOCAL_DIR:-.}"

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"

local_config_dir="$LOCAL_DIR/config"
local_presets_dir="$LOCAL_DIR/presets/animations"
remote_config_dir="~/$DEPLOY_DIR/config"
remote_presets_dir="~/$DEPLOY_DIR/presets/animations"
mask_files=(plant_pixel_map_32x138.json plant_globe_map_32x138.json)

mkdir -p "$local_config_dir" "$local_presets_dir"

echo "[INFO] Checking painter data on $PI_HOST..."
ssh $SSH_OPTS "$PI_HOST" \
    "test -d $remote_presets_dir && test -f $remote_config_dir/${mask_files[0]} && test -f $remote_config_dir/${mask_files[1]}"

echo "[INFO] Refreshing checked-in plant masks..."
for mask_file in "${mask_files[@]}"; do
    rsync -az \
        --itemize-changes \
        --omit-dir-times \
        -e "ssh $SSH_OPTS" \
        "$PI_HOST:$remote_config_dir/$mask_file" \
        "$local_config_dir/$mask_file"
done

echo "[INFO] Fetching new manually saved animation presets..."
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

echo "[SUCCESS] Wall-authored masks and presets are synchronized locally."
echo "[INFO] Inspect mask changes with: git diff -- config/plant_pixel_map_32x138.json config/plant_globe_map_32x138.json"
echo "[INFO] Fetched runtime presets remain ignored until explicitly curated."
echo "[INFO] Inspect presets with: git ls-files --others --ignored --exclude-standard 'presets/animations/**/*.json'"
echo "[INFO] Stage a preset with: git add -f presets/animations/<animation>/<preset>.json"
