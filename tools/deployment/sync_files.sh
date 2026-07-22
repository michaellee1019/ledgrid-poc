# Shared, tracked-file deployment syncs.
# Source after setting PI_HOST, DEPLOY_DIR, LOCAL_DIR, and SSH_OPTS.

DEPLOY_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

deployment_manifest() {
    local scope="$1"
    python3 "$DEPLOY_TOOLS_DIR/deploy_manifest.py" \
        --root "$LOCAL_DIR" \
        --scope "$scope" \
        --null
}

sync_full_deployment() {
    local stage_dir
    stage_dir="$(mktemp -d)"
    trap "rm -rf -- '$stage_dir'" EXIT

    # Stage only Git-tracked working-tree files. This includes local edits to
    # tracked files without leaking ignored or untracked workstation content.
    deployment_manifest full \
        | rsync -a --from0 --files-from=- "$LOCAL_DIR"/ "$stage_dir"/

    # These paths are owned by the running target. Excluding them both prevents
    # uploads and protects them from --delete while stale deployed code is
    # removed.
    rsync -az --delete --stats \
        -e "ssh $SSH_OPTS" \
        --exclude 'venv/' \
        --exclude '.venv*/' \
        --exclude 'run_state/' \
        --exclude 'presets/animations/' \
        --exclude '.esp32_firmware_hash' \
        --exclude '*.log' \
        --exclude '.pio/' \
        --exclude 'build/' \
        --exclude 'dist/' \
        --exclude 'out/' \
        "$stage_dir"/ "$PI_HOST:~/$DEPLOY_DIR/"
}

sync_fast_deployment() {
    # Fast syncs copy tracked Python/web files and plugin-owned JSON/GIF assets.
    # Runtime presets are not in this manifest and are never deletion targets.
    deployment_manifest fast \
        | rsync -az --from0 --files-from=- \
            -e "ssh $SSH_OPTS" \
            "$LOCAL_DIR"/ "$PI_HOST:~/$DEPLOY_DIR/"
}
