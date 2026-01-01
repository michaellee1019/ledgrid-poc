#!/usr/bin/env bash
set -euo pipefail

OUT_FILE=${OUT_FILE:-diagnostics/remote_diagnostics.out}
if [ -n "${OUT_FILE}" ]; then
  mkdir -p "$(dirname "${OUT_FILE}")"
  exec >"${OUT_FILE}" 2>&1
fi

REMOTE=${REMOTE:-ledwallleft@ledwallleft.local}
REMOTE_DIR=${REMOTE_DIR:-"~/ledgrid-pod"}
PORT=${PORT:-5000}
ANIMATION=${ANIMATION:-}
SLEEP_SECS=${SLEEP_SECS:-2}
SSH_OPTS=${SSH_OPTS:-"-o BatchMode=yes -o ConnectTimeout=10"}
KILL_PORT=${KILL_PORT:-0}
RESTART_WEB=${RESTART_WEB:-0}
WAIT_PORT=${WAIT_PORT:-1}

section() {
  echo
  echo "=== $1"
}

PARSE_SCRIPT=$(mktemp)
trap 'rm -f "${PARSE_SCRIPT}"' EXIT

cat >"${PARSE_SCRIPT}" <<'PY'
import json
import sys

raw = sys.stdin.read()
status = "unknown"
body = raw
if "HTTP:" in raw:
    body, _, status = raw.rpartition("HTTP:")
    status = status.strip()
try:
    data = json.loads(body)
    print(json.dumps(data, indent=2, sort_keys=True))
except Exception as exc:
    print(f"Failed to parse payload (HTTP {status}): {exc}")
    print(body.strip()[:400])
PY

section "timestamp"
date

section "remote processes"
ssh ${SSH_OPTS} "${REMOTE}" "pgrep -af 'start_server.py|gunicorn|flask' || true" || true

section "remote port ${PORT}"
ssh ${SSH_OPTS} "${REMOTE}" "ss -ltnp | grep ':${PORT}' || true" || true

if [ "${KILL_PORT}" = "1" ]; then
  section "remote port cleanup"
  ssh ${SSH_OPTS} "${REMOTE}" "pid=\$(ss -ltnp | grep ':${PORT}' | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | head -n 1); if [ -n \"\$pid\" ]; then echo \"killing pid \$pid\"; kill \"\$pid\"; else echo \"no pid found\"; fi" || true
fi

if [ "${RESTART_WEB}" = "1" ]; then
  section "remote web restart"
  ssh ${SSH_OPTS} "${REMOTE}" "REMOTE_DIR=${REMOTE_DIR} PORT=${PORT} bash -s" <<'EOS'
set -euo pipefail
REMOTE_DIR_EXPANDED="${REMOTE_DIR/#\~/$HOME}"
cd "${REMOTE_DIR_EXPANDED}"
PYTHON_BIN="python3"
if [ -x "venv/bin/python" ]; then
  PYTHON_BIN="venv/bin/python"
fi
"${PYTHON_BIN}" - <<'PY' > /tmp/ledgrid_web_params.txt
import json
from pathlib import Path
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
status_path = Path('run_state/status.json')
strips = DEFAULT_STRIP_COUNT
leds = DEFAULT_LEDS_PER_STRIP
if status_path.exists():
    try:
        data = json.loads(status_path.read_text())
        led_info = data.get('led_info') or {}
        strips = int(led_info.get('strip_count', strips) or strips)
        leds = int(led_info.get('leds_per_strip', leds) or leds)
    except Exception:
        pass
print(f"{strips} {leds}")
PY
read -r STRIPS LEDS < /tmp/ledgrid_web_params.txt
nohup "${PYTHON_BIN}" scripts/start_server.py \
  --mode web \
  --control-file run_state/control.json \
  --status-file run_state/status.json \
  --animations-dir animation/plugins \
  --strips "${STRIPS}" \
  --leds-per-strip "${LEDS}" \
  --animation-speed-scale 0.2 \
  --host 0.0.0.0 \
  --port "${PORT}" \
  > web.log 2>&1 &
echo $! > run_state/web.pid
EOS
fi

section "remote port ${PORT} after restart"
ssh ${SSH_OPTS} "${REMOTE}" "ss -ltnp | grep ':${PORT}' || true" || true

if [ "${WAIT_PORT}" = "1" ]; then
  section "wait for remote port ${PORT}"
  PORT_READY=0
  for _ in $(seq 1 10); do
    if ssh ${SSH_OPTS} "${REMOTE}" "ss -ltnp | grep ':${PORT}' >/dev/null 2>&1"; then
      PORT_READY=1
      ssh ${SSH_OPTS} "${REMOTE}" "ss -ltnp | grep ':${PORT}' || true" || true
      break
    fi
    sleep 0.5
  done
  if [ "${PORT_READY}" != "1" ]; then
    echo "port ${PORT} not listening yet"
  fi
fi

section "remote web logs"
ssh ${SSH_OPTS} "${REMOTE}" "cd ${REMOTE_DIR} && if [ -f run_state/web.pid ]; then echo \"web pid: \$(cat run_state/web.pid)\"; else echo \"web pid missing\"; fi; ls -l web.log 2>/dev/null || echo 'web.log missing'; tail -n 80 web.log 2>/dev/null || true" || true

section "remote controller logs"
ssh ${SSH_OPTS} "${REMOTE}" "cd ${REMOTE_DIR} && if [ -f run_state/controller.pid ]; then echo \"controller pid: \$(cat run_state/controller.pid)\"; else echo \"controller pid missing\"; fi; tail -n 20 controller.log 2>/dev/null || echo 'controller.log missing'" || true

section "remote status file"
ssh ${SSH_OPTS} "${REMOTE}" "cd ${REMOTE_DIR} && if [ -f run_state/status.json ]; then ls -l run_state/status.json; python3 - <<'PY'
import json
from pathlib import Path
path = Path('run_state/status.json')
try:
    data = json.loads(path.read_text())
except Exception as exc:
    print(f\"Failed to read status.json: {exc}\")
else:
    keys = sorted(data.keys())
    print(\"keys:\", keys)
    print(\"is_running:\", data.get(\"is_running\"))
    print(\"current_animation:\", data.get(\"current_animation\"))
    print(\"frame_count:\", data.get(\"frame_count\"))
    print(\"actual_fps:\", data.get(\"actual_fps\"))
    print(\"driver_stats_present:\", \"driver_stats\" in data)
PY
else
  echo \"run_state/status.json missing\"
fi" || true

if [ -n "${ANIMATION}" ]; then
  section "start animation"
  ssh ${SSH_OPTS} "${REMOTE}" "curl -s -X POST 'http://localhost:${PORT}/api/start/${ANIMATION}'" || true
  echo
  sleep "${SLEEP_SECS}"
fi

section "api status (remote)"
{ ssh ${SSH_OPTS} "${REMOTE}" "curl -sS -w '\nHTTP:%{http_code}\n' 'http://localhost:${PORT}/api/status' 2>&1" 2>&1 || true; } | python3 "${PARSE_SCRIPT}"

section "api metrics (remote)"
{ ssh ${SSH_OPTS} "${REMOTE}" "curl -sS -w '\nHTTP:%{http_code}\n' 'http://localhost:${PORT}/api/metrics' 2>&1" 2>&1 || true; } | python3 "${PARSE_SCRIPT}"

section "api hardware stats (remote)"
{ ssh ${SSH_OPTS} "${REMOTE}" "curl -sS -w '\nHTTP:%{http_code}\n' 'http://localhost:${PORT}/api/hardware/stats' 2>&1" 2>&1 || true; } | python3 "${PARSE_SCRIPT}"
