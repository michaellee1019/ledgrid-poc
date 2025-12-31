#!/bin/bash
# Flash ESP32 firmware on the deploy target when sources change.

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

DEPLOY_DIR="${DEPLOY_DIR:-$HOME/ledgrid-pod}"
FIRMWARE_DIR="$DEPLOY_DIR/firmware/esp32"
HASH_FILE="$DEPLOY_DIR/.esp32_firmware_hash"

log_info() { echo "[INFO] $1"; }
log_success() { echo "[SUCCESS] $1"; }
log_warning() { echo "[WARNING] $1"; }

PIO_CMD="pio"
if ! command -v pio >/dev/null 2>&1; then
  if [ -x "$HOME/.platformio-venv/bin/pio" ]; then
    PIO_CMD="$HOME/.platformio-venv/bin/pio"
  else
    PIO_CMD="python3 -m platformio"
  fi
fi

if ! $PIO_CMD --version >/dev/null 2>&1; then
  log_warning "PlatformIO not available; skipping ESP32 flash"
  exit 1
fi

if [ ! -d "$FIRMWARE_DIR" ]; then
  log_warning "Firmware directory not found at $FIRMWARE_DIR; skipping ESP32 flash"
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  HASH_TOOL=(sha256sum)
else
  HASH_TOOL=(shasum -a 256)
fi

log_info "Computing firmware source hash..."
current_hash="$(
  cd "$FIRMWARE_DIR"
  find platformio.ini src -type f -print0 | sort -z | xargs -0 "${HASH_TOOL[@]}" | "${HASH_TOOL[@]}" | awk '{print $1}'
)"

previous_hash=""
if [ -f "$HASH_FILE" ]; then
  previous_hash="$(cat "$HASH_FILE" | tr -d '\n')"
fi

if [ "$current_hash" = "$previous_hash" ]; then
  log_info "Firmware unchanged; skipping ESP32 flash"
  exit 0
fi

log_info "Discovering ESP32 devices..."
devices_json="$($PIO_CMD device list --json-output)"
ports="$(
  PIO_DEVICES_JSON="$devices_json" python3 - <<'PY'
import json
import os

data = json.loads(os.environ["PIO_DEVICES_JSON"])
ports = []
for entry in data:
    path = entry.get("path") or entry.get("port", "")
    if path.startswith("/dev/ttyACM") or path.startswith("/dev/ttyUSB"):
        ports.append(path)

for path in sorted(ports)[:2]:
    print(path)
PY
)"

if [ -z "$ports" ]; then
  log_warning "No ESP32 devices detected; skipping flash"
  exit 1
fi

port_count="$(echo "$ports" | wc -l | tr -d ' ')"
if [ "$port_count" -lt 2 ]; then
  log_warning "Only detected $port_count ESP32 device(s); skipping flash"
  exit 1
fi

log_info "Building firmware..."
(cd "$FIRMWARE_DIR" && $PIO_CMD run -e seeed_xiao_esp32s3)

log_info "Flashing firmware to ESP32 devices..."
while IFS= read -r port; do
  log_info "Uploading to $port"
  (cd "$FIRMWARE_DIR" && $PIO_CMD run -e seeed_xiao_esp32s3 -t upload --upload-port "$port")
done <<< "$ports"

echo "$current_hash" > "$HASH_FILE"
log_success "ESP32 firmware flashed"
