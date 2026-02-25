#!/bin/bash
# Flash ESP32 firmware on the deploy target when sources change.

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

DEBUG="${DEBUG:-0}"
log_debug() { [ "$DEBUG" = "1" ] && echo "[DEBUG] $1" || true; }

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

# Always show what's on the USB bus for diagnostics
log_info "USB serial devices in /dev:"
usb_devs="$(ls -1 /dev/ttyACM* /dev/ttyUSB* 2>/dev/null || true)"
if [ -n "$usb_devs" ]; then
  echo "$usb_devs" | while read -r d; do echo "  $d"; done
else
  echo "  (none found)"
fi

if [ "$DEBUG" = "1" ]; then
  echo "[DEBUG] All serial devices (extended):"
  ls -la /dev/ttyACM* /dev/ttyUSB* /dev/tty.usb* /dev/cu.usb* /dev/serial/by-id/* /dev/serial/by-path/* 2>/dev/null || echo "  (none found)"
  echo "[DEBUG] lsusb output:"
  lsusb 2>/dev/null || echo "  (lsusb not available)"
  echo "[DEBUG] pio device list:"
  $PIO_CMD device list 2>/dev/null || echo "  (pio device list failed)"
fi

# Discover ports: scan /dev directly for ttyACM and ttyUSB devices,
# then supplement with anything pio device list reports.
ports="$(DEBUG="$DEBUG" PIO_CMD="$PIO_CMD" python3 - <<'PY'
import glob, json, os, subprocess, sys

debug = os.environ.get("DEBUG") == "1"
found = set()

# Direct /dev scan (catches devices pio may miss)
for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
    matches = glob.glob(pattern)
    if debug and matches:
        print(f"[DEBUG] glob {pattern}: {matches}", file=sys.stderr)
    found.update(matches)

# Also check pio device list as a fallback
try:
    pio = os.environ.get("PIO_CMD", "pio").split()
    raw = subprocess.check_output(
        pio + ["device", "list", "--json-output"],
        timeout=10, stderr=subprocess.DEVNULL)
    data = json.loads(raw)
    if debug:
        print(f"[DEBUG] pio device list returned {len(data)} entries:", file=sys.stderr)
        for entry in data:
            print(f"  {entry.get('path', '?')}  hwid={entry.get('hwid', '?')}  desc={entry.get('description', '?')}", file=sys.stderr)
    for entry in data:
        path = entry.get("path") or entry.get("port", "")
        if path.startswith("/dev/ttyACM") or path.startswith("/dev/ttyUSB"):
            found.add(path)
except Exception as e:
    if debug:
        print(f"[DEBUG] pio device list failed: {e}", file=sys.stderr)

if debug:
    print(f"[DEBUG] Final detected ports: {sorted(found)}", file=sys.stderr)

for path in sorted(found):
    print(path)
PY
)"

if [ -z "$ports" ]; then
  log_warning "No ESP32 devices detected; skipping flash"
  [ "$DEBUG" = "1" ] && echo "[DEBUG] Hint: check USB connections, try 'ls /dev/ttyACM* /dev/ttyUSB*' and 'lsusb'"
  exit 1
fi

port_count="$(echo "$ports" | wc -l | tr -d ' ')"
log_info "Detected $port_count ESP32 device(s)"
while IFS= read -r p; do
  log_info "  -> $p"
done <<< "$ports"

log_info "Building firmware..."
(cd "$FIRMWARE_DIR" && $PIO_CMD run -e esp32-s3-devkitc-1)

log_info "Flashing firmware to $port_count ESP32 device(s) in parallel..."
pids=()
flash_logs=()
while IFS= read -r port; do
  log_file=$(mktemp)
  flash_logs+=("$port|$log_file")
  log_info "Uploading to $port (background)"
  (cd "$FIRMWARE_DIR" && $PIO_CMD run -e esp32-s3-devkitc-1 -t upload --upload-port "$port" > "$log_file" 2>&1) &
  pids+=($!)
done <<< "$ports"

# Wait for all uploads and report results
all_ok=true
for i in "${!pids[@]}"; do
  pid=${pids[$i]}
  entry=${flash_logs[$i]}
  port="${entry%%|*}"
  log_file="${entry##*|}"
  if wait "$pid"; then
    log_success "Flashed $port"
  else
    log_warning "Flash FAILED for $port"
    cat "$log_file"
    all_ok=false
  fi
  rm -f "$log_file"
done

if $all_ok; then
  echo "$current_hash" > "$HASH_FILE"
  log_success "All $port_count ESP32 device(s) flashed successfully"
else
  log_warning "Some devices failed to flash; hash NOT updated (will retry next deploy)"
fi
