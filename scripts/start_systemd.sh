#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d "venv" ]; then
    echo "Virtual environment not found at $ROOT_DIR/venv" >&2
    exit 1
fi

source venv/bin/activate

DEFAULT_STRIPS=$(python - <<'PY'
from drivers.led_layout import DEFAULT_STRIP_COUNT
print(DEFAULT_STRIP_COUNT)
PY
)
DEFAULT_LEDS_PER_STRIP=$(python - <<'PY'
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP
print(DEFAULT_LEDS_PER_STRIP)
PY
)

STRIPS=${STRIPS:-$DEFAULT_STRIPS}
LEDS_PER_STRIP=${LEDS_PER_STRIP:-$DEFAULT_LEDS_PER_STRIP}
TARGET_FPS=${TARGET_FPS:-40}
ANIMATION_SPEED_SCALE=${ANIMATION_SPEED_SCALE:-0.2}
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-5000}
CONTROL_FILE=${CONTROL_FILE:-run_state/control.json}
STATUS_FILE=${STATUS_FILE:-run_state/status.json}
ANIM_DIR=${ANIM_DIR:-animation/plugins}
POLL_INTERVAL=${POLL_INTERVAL:-0.5}
STATUS_INTERVAL=${STATUS_INTERVAL:-0.5}

export PYTHONUNBUFFERED=1

mkdir -p "$(dirname "$CONTROL_FILE")" "$(dirname "$STATUS_FILE")"

python scripts/start_server.py \
    --mode controller \
    --control-file "$CONTROL_FILE" \
    --status-file "$STATUS_FILE" \
    --animations-dir "$ANIM_DIR" \
    --strips "$STRIPS" \
    --leds-per-strip "$LEDS_PER_STRIP" \
    --target-fps "$TARGET_FPS" \
    --animation-speed-scale "$ANIMATION_SPEED_SCALE" \
    --poll-interval "$POLL_INTERVAL" \
    --status-interval "$STATUS_INTERVAL" \
    > controller.log 2>&1 &
CONTROLLER_PID=$!
echo "$CONTROLLER_PID" > run_state/controller.pid

python scripts/start_server.py \
    --mode web \
    --control-file "$CONTROL_FILE" \
    --status-file "$STATUS_FILE" \
    --animations-dir "$ANIM_DIR" \
    --strips "$STRIPS" \
    --leds-per-strip "$LEDS_PER_STRIP" \
    --animation-speed-scale "$ANIMATION_SPEED_SCALE" \
    --host "$HOST" \
    --port "$PORT" \
    > web.log 2>&1 &
WEB_PID=$!
echo "$WEB_PID" > run_state/web.pid

cleanup() {
    kill "$CONTROLLER_PID" "$WEB_PID" 2>/dev/null || true
    wait "$CONTROLLER_PID" "$WEB_PID" 2>/dev/null || true
}
trap cleanup TERM INT

set +e
wait -n "$CONTROLLER_PID" "$WEB_PID"
EXIT_STATUS=$?
set -e

cleanup
exit "$EXIT_STATUS"
