#!/bin/bash
# Build and flash four explicitly provisioned receiver images on the deploy target.

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

DEBUG="${DEBUG:-0}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/ledgrid-pod}"
FIRMWARE_DIR="$DEPLOY_DIR/firmware/esp32"
STATE_DIR="$DEPLOY_DIR/run_state/firmware"
CONFIG_FILE="$STATE_DIR/deploy.env"
PUBLIC_KEY="$STATE_DIR/public.pem"
HASH_FILE="$DEPLOY_DIR/.esp32_firmware_hash"

log_info() { echo "[INFO] $1"; }
log_success() { echo "[SUCCESS] $1"; }
log_warning() { echo "[WARNING] $1"; }
log_debug() { [ "$DEBUG" = "1" ] && echo "[DEBUG] $1" || true; }

PIO_CMD="pio"
if ! command -v pio >/dev/null 2>&1; then
  if [ -x "$HOME/.platformio-venv/bin/pio" ]; then
    PIO_CMD="$HOME/.platformio-venv/bin/pio"
  else
    PIO_CMD="python3 -m platformio"
  fi
fi

if ! $PIO_CMD --version >/dev/null 2>&1; then
  log_warning "PlatformIO is required to flash receiver firmware"
  exit 1
fi
if [ ! -d "$FIRMWARE_DIR" ]; then
  log_warning "Firmware directory not found at $FIRMWARE_DIR"
  exit 1
fi
if [ ! -f "$CONFIG_FILE" ] || [ ! -f "$PUBLIC_KEY" ]; then
  log_warning "Signed receiver provisioning is missing from $STATE_DIR"
  log_warning "Run the documented 'just provision-native-animations' command first"
  exit 1
fi

python3 "$DEPLOY_DIR/tools/deployment/firmware_provisioning.py" check \
  --config "$CONFIG_FILE" --public-key "$PUBLIC_KEY"
# firmware_provisioning.py accepts only a fixed key set and shell-safe values.
set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

ports=(
  "$LEDGRID_RECEIVER_0_PORT"
  "$LEDGRID_RECEIVER_1_PORT"
  "$LEDGRID_RECEIVER_2_PORT"
  "$LEDGRID_RECEIVER_3_PORT"
)
resolved_ports=()
for logical_device in 0 1 2 3; do
  port="${ports[$logical_device]}"
  if [ ! -e "$port" ]; then
    log_warning "Receiver $logical_device port is missing: $port"
    exit 1
  fi
  resolved="$(readlink -f "$port")"
  if [[ " ${resolved_ports[*]} " == *" $resolved "* ]]; then
    log_warning "Receiver port aliases resolve to the same device: $port"
    exit 1
  fi
  resolved_ports+=("$resolved")
  log_info "Receiver $logical_device -> $port ($resolved)"
done

log_info "Computing firmware and provisioning hash..."
source_hash="$(
  LEDGRID_LOGICAL_DEVICE=all \
  python3 "$DEPLOY_DIR/tools/deployment/firmware_build_hash.py" "$FIRMWARE_DIR"
)"
config_hash="$(sha256sum "$CONFIG_FILE" "$PUBLIC_KEY" | sha256sum | awk '{print $1}')"
current_hash="$(printf '%s\n%s\n' "$source_hash" "$config_hash" | sha256sum | awk '{print $1}')"
previous_hash=""
if [ -f "$HASH_FILE" ]; then
  previous_hash="$(tr -d '\n' < "$HASH_FILE")"
fi
if [ "$current_hash" = "$previous_hash" ]; then
  log_info "Firmware and provisioning unchanged; skipping ESP32 flash"
  exit 0
fi

build_root="$STATE_DIR/build"
mkdir -p "$build_root"
for logical_device in 0 1 2 3; do
  environment="receiver-$logical_device"
  device_root="$build_root/$environment"
  project_config="$device_root/platformio.ini"
  mkdir -p "$device_root"
  python3 "$DEPLOY_DIR/tools/deployment/firmware_provisioning.py" \
    platformio-config --config "$CONFIG_FILE" --public-key "$PUBLIC_KEY" \
    --logical-device "$logical_device" --build-dir "$device_root/.pio" \
    --output "$project_config" \
    --sdkconfig-defaults "$FIRMWARE_DIR/sdkconfig.defaults" \
    --sdkconfig-output "$FIRMWARE_DIR/sdkconfig.$environment"

  log_info "Building signed receiver image for logical device $logical_device..."
  (cd "$FIRMWARE_DIR" && $PIO_CMD run -c "$project_config" -e "$environment")

  sdkconfig="$FIRMWARE_DIR/sdkconfig.$environment"
  grep -Fqx "CONFIG_LEDGRID_TRUSTED_KEY_ID=\"$LEDGRID_TRUSTED_KEY_ID\"" "$sdkconfig"
  grep -Fqx "CONFIG_LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX=\"$LEDGRID_TRUSTED_P256_PUBLIC_KEY_HEX\"" "$sdkconfig"
  grep -Fqx "CONFIG_LEDGRID_LOGICAL_DEVICE=$logical_device" "$sdkconfig"
  grep -Fqx "# CONFIG_LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT is not set" "$sdkconfig"

  port="${ports[$logical_device]}"
  log_info "Flashing logical device $logical_device via $port..."
  (cd "$FIRMWARE_DIR" && $PIO_CMD run -c "$project_config" -e "$environment" \
    -t upload --upload-port "$port")
  log_success "Flashed logical device $logical_device"
done

printf '%s\n' "$current_hash" > "$HASH_FILE"
log_success "All four provisioned receiver images flashed successfully"
