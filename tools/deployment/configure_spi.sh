#!/bin/bash
# Configure SPI on the Raspberry Pi (idempotent).
# Runs on the Pi (invoked remotely by deploy.sh).
#
# Wall layout (LEDGRID_HAT=0, default): 4 ESP32s x 8 strips = 32 strips
#   /dev/spidev0.0 /dev/spidev0.1 /dev/spidev1.0 /dev/spidev1.1
#
# HAT layout (LEDGRID_HAT=1): 2 ESP32 modules x 8 strips = 16 strips
#   /dev/spidev0.0  ESP1 on SPI0 CE0
#   /dev/spidev0.1  ESP2 on SPI0 CE1

set -euo pipefail

is_hat_layout() {
  case "${LEDGRID_HAT:-0}" in
    1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

if is_hat_layout; then
  REQUIRED_DEVICES=(
    "/dev/spidev0.0"
    "/dev/spidev0.1"
  )
else
  REQUIRED_DEVICES=(
    "/dev/spidev0.0"
    "/dev/spidev0.1"
    "/dev/spidev1.0"
    "/dev/spidev1.1"
  )
fi

CONFIG_CHANGED=0

log_info() { echo "[INFO] $1"; }
log_success() { echo "[SUCCESS] $1"; }
log_warning() { echo "[WARNING] $1"; }

find_config_files() {
  CONFIG_FILES=()
  if [ -f /boot/firmware/config.txt ]; then
    CONFIG_FILES+=("/boot/firmware/config.txt")
  fi
  if [ -f /boot/config.txt ]; then
    CONFIG_FILES+=("/boot/config.txt")
  fi

  if [ ${#CONFIG_FILES[@]} -eq 0 ]; then
    echo "[ERROR] Could not find a boot config file." >&2
    exit 1
  fi
}

desired_spi_lines() {
  if is_hat_layout; then
    printf '%s\n' "dtparam=spi=on"
  else
    printf '%s\n' \
      "dtparam=spi=on" \
      "dtoverlay=spi1-2cs"
  fi
}

config_is_correct() {
  local cfg="$1"
  local line

  if is_hat_layout; then
    if ! sudo grep -qE '^[[:space:]]*dtparam=spi=on' "$cfg" 2>/dev/null; then
      return 1
    fi
    if sudo grep -qE '^[[:space:]]*dtparam=spi=off' "$cfg" 2>/dev/null; then
      return 1
    fi
    if sudo grep -qE '^[[:space:]]*dtoverlay=spi' "$cfg" 2>/dev/null; then
      return 1
    fi
    return 0
  fi

  while IFS= read -r line; do
    if ! sudo grep -qF "$line" "$cfg" 2>/dev/null; then
      return 1
    fi
  done < <(desired_spi_lines)

  return 0
}

apply_boot_config() {
  local cfg="$1"

  if config_is_correct "$cfg"; then
    log_success "Boot config already correct: ${cfg}"
    return 0
  fi

  log_info "Updating SPI settings in ${cfg}"
  CONFIG_CHANGED=1

  sudo cp "$cfg" "${cfg}.ledgrid.bak"

  sudo sed -i \
    -e '/^[[:space:]]*dtparam=spi=/d' \
    -e '/^[[:space:]]*dtoverlay=spi/d' \
    "$cfg"

  while IFS= read -r line; do
    echo "$line" | sudo tee -a "$cfg" >/dev/null
  done < <(desired_spi_lines)

  log_info "Applied to ${cfg}:"
  desired_spi_lines | while IFS= read -r line; do
    log_info "  ${line}"
  done
  if is_hat_layout; then
    log_info "  HAT mode: ESP1=spidev0.0, ESP2=spidev0.1 (shared SPI0 bus)"
  fi
}

spi_devices_present() {
  local dev
  for dev in "${REQUIRED_DEVICES[@]}"; do
    if [ ! -e "$dev" ]; then
      return 1
    fi
  done
  return 0
}

list_spi_devices() {
  ls -l /dev/spidev* 2>/dev/null || true
}

missing_spi_devices() {
  local dev
  for dev in "${REQUIRED_DEVICES[@]}"; do
    if [ ! -e "$dev" ]; then
      echo "$dev"
    fi
  done
}

find_config_files
TARGET_CONFIG="${CONFIG_FILES[0]}"
apply_boot_config "$TARGET_CONFIG"

if spi_devices_present && [ "$CONFIG_CHANGED" -eq 0 ]; then
  log_success "All required SPI devices are present"
  list_spi_devices
  echo "STATUS=ready"
  echo "CONFIG_CHANGED=${CONFIG_CHANGED}"
  exit 0
fi

if [ "$CONFIG_CHANGED" -eq 1 ]; then
  log_info "Boot config changed; reboot required to apply device-tree overlays"
elif ! spi_devices_present; then
  log_warning "Missing SPI devices:"
  while IFS= read -r dev; do
    [ -n "$dev" ] && log_warning "  $dev"
  done < <(missing_spi_devices)
  log_info "Reboot required to expose SPI device nodes"
fi

echo "STATUS=needs_reboot"
echo "CONFIG_CHANGED=${CONFIG_CHANGED}"
exit 0
