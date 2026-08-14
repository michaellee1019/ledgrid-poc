# Shared, tracked-file deployment syncs.
# Source after setting PI_HOST, DEPLOY_DIR, LOCAL_DIR, and SSH_OPTS.

DEPLOY_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREVIEW_ARTIFACT_DIR="$LOCAL_DIR/web/static/generated/animation-previews"

generate_preview_artifacts() {
    uv run --with numpy --with pillow python \
        "$LOCAL_DIR/tools/generate_animation_previews.py" --tracked-only \
        --workers "${PREVIEW_WORKERS:-0}"
}

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

    generate_preview_artifacts

    # Stage only Git-tracked working-tree files. This includes local edits to
    # tracked files without leaking ignored or untracked workstation content.
    deployment_manifest full \
        | rsync -a --from0 --files-from=- "$LOCAL_DIR"/ "$stage_dir"/
    mkdir -p "$stage_dir/web/static/generated/animation-previews"
    rsync -a --delete \
        "$PREVIEW_ARTIFACT_DIR"/ \
        "$stage_dir/web/static/generated/animation-previews"/

    # These paths are owned by the running target. Runtime presets get an
    # explicit receiver-side protection rule as well as a sender-side exclude:
    # even when the staging tree has no presets directory, --delete may not
    # remove presets saved through the deployed UI.
    rsync -az --delete --stats \
        -e "ssh $SSH_OPTS" \
        --filter 'protect /current' \
        --filter 'protect /releases/***' \
        --filter 'protect /.incoming/***' \
        --filter 'protect /receipts/***' \
        --filter 'protect /calibration_photos/***' \
        --filter 'protect /receiver_library/***' \
        --filter 'protect /installation_profile_library/***' \
        --exclude 'venv/' \
        --exclude '.venv*/' \
        --exclude '.venvs/' \
        --exclude 'run_state/' \
        --filter 'protect /presets/' \
        --filter 'protect /presets/animations/***' \
        --exclude '/presets/animations/' \
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
    generate_preview_artifacts
    deployment_manifest fast \
        | rsync -az --from0 --files-from=- \
            -e "ssh $SSH_OPTS" \
            "$LOCAL_DIR"/ "$PI_HOST:~/$DEPLOY_DIR/"
    # Generated previews are intentionally ignored and therefore absent from
    # the tracked manifest. Delete is tightly scoped to this derived directory.
    rsync -az --delete \
        -e "ssh $SSH_OPTS" \
        "$PREVIEW_ARTIFACT_DIR"/ \
        "$PI_HOST:~/$DEPLOY_DIR/web/static/generated/animation-previews/"
}
