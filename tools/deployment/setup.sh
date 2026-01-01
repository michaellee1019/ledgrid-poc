#!/bin/bash
# Prepare the deploy target for ESP32 flashing and app runtime.

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

PI_HOST="${PI_HOST:-ledwallleft@ledwallleft.local}"
DEPLOY_DIR="${DEPLOY_DIR:-ledgrid-pod}"
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"

log_info() { echo "[INFO] $1"; }
log_success() { echo "[SUCCESS] $1"; }
log_warning() { echo "[WARNING] $1"; }
log_error() { echo "[ERROR] $1"; }

log_info "Testing SSH connection to $PI_HOST..."
if ssh $SSH_OPTS "$PI_HOST" "echo ok" >/dev/null 2>&1; then
  log_success "SSH connection working"
else
  log_error "Cannot connect to $PI_HOST via SSH"
  exit 1
fi

log_info "Ensuring PlatformIO is available on the deploy target..."
ssh $SSH_OPTS "$PI_HOST" "bash -s" <<'EOF'
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"

if command -v pio >/dev/null 2>&1; then
  echo "[SUCCESS] PlatformIO already installed"
  exit 0
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "[INFO] python3-venv missing; installing via apt"
  sudo apt-get update
  sudo apt-get install -y python3-venv
fi

VENV_DIR="$HOME/.platformio-venv"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install --upgrade platformio

mkdir -p "$HOME/.local/bin"
ln -sf "$VENV_DIR/bin/pio" "$HOME/.local/bin/pio"

echo "[SUCCESS] PlatformIO installed (venv at $VENV_DIR)"
EOF

log_info "Ensuring PlatformIO is usable..."
if ! ssh $SSH_OPTS "$PI_HOST" "bash -s" <<'EOF'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

if command -v pio >/dev/null 2>&1; then
  pio --version >/dev/null 2>&1
  exit 0
fi

if [ -x "$HOME/.platformio-venv/bin/pio" ]; then
  "$HOME/.platformio-venv/bin/pio" --version >/dev/null 2>&1
  exit 0
fi

echo "[ERROR] PlatformIO not found after setup"
exit 1
EOF
then
  log_error "PlatformIO is not available on the deploy target"
  exit 1
fi

log_info "Ensuring deploy directory exists..."
ssh $SSH_OPTS "$PI_HOST" "mkdir -p ~/$DEPLOY_DIR"

log_info "Checking serial permissions..."
ssh $SSH_OPTS "$PI_HOST" "bash -s" <<'EOF'
set -euo pipefail

if groups | tr ' ' '\n' | grep -q '^dialout$'; then
  echo "[SUCCESS] User already in dialout group"
  exit 0
fi

echo "[INFO] Adding user to dialout group (requires sudo)"
sudo usermod -a -G dialout "$USER"
echo "[SUCCESS] Added user to dialout group; re-login may be required"
EOF

log_info "Detecting connected ESP32 devices..."
if ! ssh $SSH_OPTS "$PI_HOST" "bash -s" <<'EOF'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

PIO_CMD="pio"
if ! command -v pio >/dev/null 2>&1; then
  if [ -x "$HOME/.platformio-venv/bin/pio" ]; then
    PIO_CMD="$HOME/.platformio-venv/bin/pio"
  else
    PIO_CMD="python3 -m platformio"
  fi
fi

if ! $PIO_CMD device list --json-output >/dev/null 2>&1; then
  echo "[ERROR] Unable to list devices via PlatformIO"
  exit 1
fi

PIO_CMD="$PIO_CMD" python3 - <<'PY'
import json
import os
import subprocess

pio_cmd = os.environ["PIO_CMD"].split()
cmd = pio_cmd + ["device", "list", "--json-output"]
try:
    data = json.loads(subprocess.check_output(cmd, text=True))
except Exception:
    raise

ports = []
for entry in data:
    path = entry.get("path") or entry.get("port", "")
    if path.startswith("/dev/ttyACM") or path.startswith("/dev/ttyUSB"):
        ports.append(path)

ports = sorted(ports)
print(f"[INFO] Detected {len(ports)} USB serial device(s): {ports}")
if len(ports) < 2:
    print("[ERROR] Expected 2 ESP32 devices for flashing")
    raise SystemExit(1)
PY
EOF
then
  log_error "ESP32 devices not ready for flashing"
  exit 1
fi

log_success "Remote setup complete"
