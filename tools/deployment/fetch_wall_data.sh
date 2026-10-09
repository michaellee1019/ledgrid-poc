#!/bin/bash
# Refresh editable wall masks and retrieve custom presets for local review.
set -euo pipefail
source "$(dirname "$0")/ssh_helpers.sh"
LOCAL_DIR="${LOCAL_DIR:-.}"
mkdir -p "$LOCAL_DIR/config" "$LOCAL_DIR/presets/animations"
for mask in plant_pixel_map_32x138.json plant_globe_map_32x138.json; do
    rsync -az -e "$SSH_RSYNC" "$PI_HOST:$DEPLOY_DIR/data/config/$mask" "$LOCAL_DIR/config/$mask"
done
rsync -az --ignore-existing -e "$SSH_RSYNC" "$PI_HOST:$DEPLOY_DIR/data/presets/animations/" "$LOCAL_DIR/presets/animations/"
