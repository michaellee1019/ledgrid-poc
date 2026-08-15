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

pio_version="$($PIO_CMD --version 2>/dev/null || true)"
if [ -z "$pio_version" ]; then
  log_warning "PlatformIO not available; skipping ESP32 flash"
  exit 1
fi
if ! grep -q 'version 6\.1\.19$' <<< "$pio_version"; then
  log_warning "PlatformIO 6.1.19 is required; found: $pio_version"
  log_warning "Run 'just setup' to install the pinned firmware build tool"
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

firmware_environment="${FIRMWARE_ENVIRONMENT:-esp32-s3-devkitc-1}"
case "$firmware_environment" in
  esp32-s3-devkitc-1|esp32-s3-devkitc-1-local-canary) ;;
  *)
    log_warning "Unsupported firmware environment: $firmware_environment"
    exit 1
    ;;
esac
firmware_binary="$FIRMWARE_DIR/.pio/build/$firmware_environment/firmware.bin"
expected_firmware_sha256="${EXPECTED_FIRMWARE_SHA256:-}"
expected_installation_digest="${EXPECTED_FIRMWARE_INSTALLATION_DIGEST:-}"
expected_hash_file="${EXPECTED_FIRMWARE_HASH_FILE:-}"
if [ -n "$expected_firmware_sha256" ] \
    && ! [[ "$expected_firmware_sha256" =~ ^[0-9a-f]{64}$ ]]; then
  log_warning "Expected firmware digest is malformed"
  exit 1
fi
if [ -n "$expected_installation_digest" ] \
    && ! [[ "$expected_installation_digest" =~ ^[0-9a-f]{64}$ ]]; then
  log_warning "Expected firmware installation digest is malformed"
  exit 1
fi

artifact_inspector="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/firmware_artifacts.py"
if [ ! -f "$artifact_inspector" ]; then
  log_warning "Firmware artifact inspector is missing: $artifact_inspector"
  exit 1
fi

verify_firmware_installation() {
  inspector_args=(
    --firmware-dir "$FIRMWARE_DIR"
    --environment "$firmware_environment"
    --field installation_digest
  )
  if [ -n "$expected_installation_digest" ]; then
    inspector_args+=(--expect-digest "$expected_installation_digest")
  fi
  if ! actual_installation_digest="$(python3 "$artifact_inspector" "${inspector_args[@]}")"; then
    log_warning "Validated firmware installation artifacts changed or are incomplete"
    return 1
  fi
  actual_firmware_sha256="$("${HASH_TOOL[@]}" "$firmware_binary" | awk '{print $1}')"
  if [ -n "$expected_firmware_sha256" ] \
      && [ "$actual_firmware_sha256" != "$expected_firmware_sha256" ]; then
    log_warning "Validated application firmware digest changed before upload"
    return 1
  fi
}

# Verify or build the exact selected artifact before consulting the installed
# marker.  This prevents an old source-only marker from skipping a different
# coordinator-validated binary or PlatformIO environment.
if [ "${FIRMWARE_PREBUILT:-0}" = "1" ]; then
  if [ -z "$expected_firmware_sha256" ]; then
    log_warning "Prebuilt firmware requires EXPECTED_FIRMWARE_SHA256"
    exit 1
  fi
  log_info "Using coordinator-validated $firmware_environment firmware build"
  if [ -z "$expected_installation_digest" ]; then
    log_warning "Prebuilt firmware requires EXPECTED_FIRMWARE_INSTALLATION_DIGEST"
    exit 1
  fi
  verify_firmware_installation
else
  log_info "Building $firmware_environment firmware..."
  (cd "$FIRMWARE_DIR" && $PIO_CMD run -e "$firmware_environment")
  verify_firmware_installation
fi

log_info "Computing firmware installation digest..."
current_hash="$actual_installation_digest"

previous_hash=""
resolve_hash_storage() {
  local resolved resolved_expected
  resolved="$(readlink -f -- "$HASH_FILE" 2>/dev/null || true)"
  if [ -z "$resolved" ] || [ ! -f "$resolved" ]; then
    log_warning "Firmware marker cannot be resolved to a regular file" >&2
    return 1
  fi
  if [ -n "$expected_hash_file" ]; then
    if [ -L "$expected_hash_file" ] || [ ! -f "$expected_hash_file" ]; then
      log_warning "Expected shared firmware marker is not a regular file" >&2
      return 1
    fi
    resolved_expected="$(readlink -f -- "$expected_hash_file" 2>/dev/null || true)"
    if [ -z "$resolved_expected" ] || [ "$resolved" != "$resolved_expected" ]; then
      log_warning "Firmware marker does not resolve to the expected shared file" >&2
      return 1
    fi
  fi
  printf '%s\n' "$resolved"
}

hash_storage="$(resolve_hash_storage)"
previous_hash="$(tr -d '\n' < "$hash_storage")"

# Schema-v1 markers were also bare SHA-256 values, so they remain readable.
# Their source-only identity intentionally differs from the v2 artifact/layout
# identity and causes one safe migration flash; subsequent unchanged deploys
# retain the same marker and are true no-ops.
if [ "$current_hash" = "$previous_hash" ]; then
  log_info "Firmware unchanged; skipping ESP32 flash"
  printf 'FIRMWARE_INSTALLATION_DIGEST=%s\n' "$current_hash"
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

log_info "Flashing firmware to $port_count ESP32 device(s) sequentially..."
all_ok=true
while IFS= read -r port; do
  log_file=$(mktemp)
  log_info "Uploading to $port"
  # Uploads are deliberately serialized because PlatformIO mutates the common
  # .pio/build tree and SCons signature database while processing the upload
  # target. Do not combine its separate skip-build and upload targets: the
  # pinned ESP32 platform can pass malformed address/file pairs to esptool for
  # that target combination. The ordinary upload target performs an incremental
  # graph check and then reuses the coordinator-validated build.
  if ! verify_firmware_installation; then
    all_ok=false
    rm -f "$log_file"
    break
  fi
  if (
    cd "$FIRMWARE_DIR"
    $PIO_CMD run -e "$firmware_environment" -t upload \
      --upload-port "$port" > "$log_file" 2>&1
  ); then
    log_success "Flashed $port"
  else
    log_warning "Flash FAILED for $port"
    cat "$log_file"
    all_ok=false
  fi
  rm -f "$log_file"
  if ! verify_firmware_installation; then
    all_ok=false
    break
  fi
done <<< "$ports"

if $all_ok; then
  # Update the target-owned shared file, not the workspace symlink itself.
  # Replacing the symlink would strand the new digest in an ephemeral build
  # workspace and force every subsequent deploy to reflash all receivers.
  validated_hash_storage="$(resolve_hash_storage)"
  if [ "$validated_hash_storage" != "$hash_storage" ]; then
    log_warning "Firmware marker changed during flash; hash NOT updated"
    exit 1
  fi
  marker_temporary="$(mktemp "${validated_hash_storage}.tmp.XXXXXX")"
  printf '%s\n' "$current_hash" > "$marker_temporary"
  mv "$marker_temporary" "$validated_hash_storage"
  printf 'FIRMWARE_INSTALLATION_DIGEST=%s\n' "$current_hash"
  log_success "All $port_count ESP32 device(s) flashed successfully"
else
  log_warning "Some devices failed to flash; hash NOT updated (will retry next deploy)"
  exit 1
fi
