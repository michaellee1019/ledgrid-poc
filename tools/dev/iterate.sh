#!/usr/bin/env bash
set -euo pipefail

PI_HOST=${PI_HOST:-ledwallleft@ledwallleft.local}
DEPLOY_DIR=${DEPLOY_DIR:-ledgrid-pod}
LOCAL_ANIM_DIR=${LOCAL_ANIM_DIR:-animation/plugins}
REMOTE_ANIM_DIR=${REMOTE_ANIM_DIR:-animation/plugins}
CONTROL_FILE=${CONTROL_FILE:-run_state/control.json}
STATUS_FILE=${STATUS_FILE:-run_state/status.json}
START_CMD=${START_CMD:-"cd ~/$DEPLOY_DIR && nohup ./start.sh > animation_system.log 2>&1 </dev/null &"}

log() {
    printf "[%s] %s\n" "$(date +"%H:%M:%S")" "$1"
}

if ! command -v rsync >/dev/null 2>&1; then
    echo "rsync is required for iterate; please install rsync." >&2
    exit 1
fi

if ! command -v ssh >/dev/null 2>&1; then
    echo "ssh is required for iterate; please install openssh client." >&2
    exit 1
fi

tmp_dir=$(mktemp -d)
restore_path="$tmp_dir/restore.json"
status_path="$tmp_dir/status.json"

cleanup() {
    rm -rf "$tmp_dir"
}
trap cleanup EXIT

log "Saving current animation state (best effort)"
if ssh "$PI_HOST" "test -f ~/$DEPLOY_DIR/$STATUS_FILE"; then
    ssh "$PI_HOST" "cat ~/$DEPLOY_DIR/$STATUS_FILE" > "$status_path"
    STATUS_PATH="$status_path" OUT_PATH="$restore_path" python3 - <<'PY'
import json
import os

status_path = os.environ["STATUS_PATH"]
out_path = os.environ["OUT_PATH"]

try:
    with open(status_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {}

animation = data.get("current_animation")
info = data.get("animation_info") or {}
if not animation:
    animation = info.get("name")

params = {}
if isinstance(info, dict):
    params = info.get("current_params") or {}

payload = {
    "animation": animation,
    "params": params,
    "is_running": bool(data.get("is_running")),
}

with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
PY
else
    log "No remote status file; skipping state capture"
fi

log "Stopping remote animation server"
ssh "$PI_HOST" "pkill -f 'start_server.py' || true"

log "Uploading changed animation files"
rsync -az --checksum --exclude '__pycache__' --exclude '*.pyc' \
    "$LOCAL_ANIM_DIR/" "$PI_HOST:~/$DEPLOY_DIR/$REMOTE_ANIM_DIR/"

log "Restarting remote animation server"
ssh "$PI_HOST" "$START_CMD"
sleep 2

if [ -f "$restore_path" ]; then
    payload_json=$(python3 - <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

if not payload.get("is_running") or not payload.get("animation"):
    sys.exit(0)

control_payload = {
    "action": "start",
    "data": {
        "animation": payload["animation"],
        "config": payload.get("params") or {},
    },
}

print(json.dumps(control_payload, separators=(",", ":")))
PY
"$restore_path")

    if [ -n "${payload_json}" ]; then
        animation_name=$(python3 - <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fh:
    payload = json.load(fh)

print(payload.get("animation") or "")
PY
"$restore_path")
        payload_b64=$(printf "%s" "$payload_json" | base64 | tr -d '\n')
        log "Restoring animation ${animation_name}"
        ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && CONTROL_PATH='$CONTROL_FILE' PAYLOAD_B64='$payload_b64' python3 - <<'PY'
import base64
import json
import os
import time
from pathlib import Path

payload = json.loads(base64.b64decode(os.environ["PAYLOAD_B64"]))
path = Path(os.environ.get("CONTROL_PATH", "run_state/control.json"))
path.parent.mkdir(parents=True, exist_ok=True)

command_id = time.time()
payload.setdefault("command_id", command_id)
payload.setdefault("written_at", command_id)

with path.open("w", encoding="utf-8") as fh:
    json.dump(payload, fh, separators=(",", ":"))
PY"
    else
        log "No running animation to restore"
    fi
else
    log "No saved state to restore"
fi

log "Done"
