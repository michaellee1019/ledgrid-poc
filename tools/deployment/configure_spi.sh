#!/bin/bash
# Configure SPI on the Raspberry Pi (idempotent).
# Runs on the Pi through the current coordinator target helper.
#
# Wall layout (LEDGRID_HAT=0, default): 5 ESP32s, 33 strips
#   /dev/spidev0.0 /dev/spidev0.1 /dev/spidev0.3 /dev/spidev1.0 /dev/spidev1.1
#   The fifth receiver is SPI0 CE3. V6 routes that net through SJ_SPI0_CE3 to
#   GPIO6 (header pin 31). SJ_SPI0_CE2 is GPIO5 (pin 29) and is exposed as
#   spidev0.2 for the unused socket on the third HAT. Columns 1-16 stay on
#   SPI1 CE0/CE1 (GPIO22 pin 15, GPIO17 pin 11).
#
# Alternate HAT compatibility layout (LEDGRID_HAT=1): 2 ESP32 modules x 8
# strips = 16 strips. The first assembled V6 HAT closed the SPI1 jumper set
# (CE0 on GPIO22 / pin 15, CE1 on GPIO17 / pin 11), not SPI0.
#   /dev/spidev1.0  ESP1 on SPI1 CE0 (GPIO22)
#   /dev/spidev1.1  ESP2 on SPI1 CE1 (GPIO17)

set -euo pipefail

is_hat_layout() {
  case "${LEDGRID_HAT:-0}" in
    1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

if is_hat_layout; then
  REQUIRED_DEVICES=(
    "/dev/spidev1.0"
    "/dev/spidev1.1"
  )
else
  REQUIRED_DEVICES=(
    "/dev/spidev0.0"
    "/dev/spidev0.1"
    "/dev/spidev0.3"
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
    printf '%s\n' \
      "dtparam=spi=on" \
      "dtoverlay=spi1-2cs,cs0_pin=22"
  else
    printf '%s\n' \
      "dtparam=spi=on" \
      "dtoverlay=spi0-4cs" \
      "dtoverlay=spi1-2cs,cs0_pin=22"
  fi
}

config_is_correct() {
  local cfg="$1"

  if is_hat_layout; then
    if [ "$(sudo grep -cE '^[[:space:]]*dtparam=spi=' "$cfg" 2>/dev/null || true)" -ne 1 ]; then
      return 1
    fi
    if ! sudo grep -qE '^[[:space:]]*dtparam=spi=on[[:space:]]*$' "$cfg" 2>/dev/null; then
      return 1
    fi
    if [ "$(sudo grep -cE '^[[:space:]]*dtoverlay=spi' "$cfg" 2>/dev/null || true)" -ne 1 ]; then
      return 1
    fi
    if ! sudo grep -qE '^[[:space:]]*dtoverlay=spi1-2cs,cs0_pin=22[[:space:]]*$' "$cfg" 2>/dev/null; then
      return 1
    fi
    return 0
  fi

  # One exact SPI enable, the extra SPI0 CS overlay, and SPI1 two-CS with
  # V6 CE0 on GPIO22. A leftover spi1-3cs line can keep GPIO24 claimed and
  # hide the fact that column 33 is on SPI0 CE3.
  if [ "$(sudo grep -cE '^[[:space:]]*dtparam=spi=' "$cfg" 2>/dev/null || true)" -ne 1 ]; then
    return 1
  fi
  if ! sudo grep -qE '^[[:space:]]*dtparam=spi=on[[:space:]]*$' "$cfg" 2>/dev/null; then
    return 1
  fi
  if [ "$(sudo grep -cE '^[[:space:]]*dtoverlay=spi' "$cfg" 2>/dev/null || true)" -ne 2 ]; then
    return 1
  fi
  if ! sudo grep -qE '^[[:space:]]*dtoverlay=spi0-4cs[[:space:]]*$' "$cfg" 2>/dev/null; then
    return 1
  fi
  if ! sudo grep -qE '^[[:space:]]*dtoverlay=spi1-2cs,cs0_pin=22[[:space:]]*$' "$cfg" 2>/dev/null; then
    return 1
  fi

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
    log_info "  HAT mode: ESP1=spidev1.0, ESP2=spidev1.1 (SPI1 CE0=GPIO22, CE1=GPIO17)"
  fi
}

install_spi0_extra_cs_overlay() {
  local script_dir dts dest tmp overlays

  if is_hat_layout; then
    return 0
  fi

  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  dts="${script_dir}/overlays/spi0-4cs-overlay.dts"
  if [ ! -f "$dts" ]; then
    echo "[ERROR] Missing SPI0 extra-CS overlay source: ${dts}" >&2
    exit 1
  fi

  if [ -d /boot/firmware/overlays ]; then
    overlays="/boot/firmware/overlays"
  elif [ -d /boot/overlays ]; then
    overlays="/boot/overlays"
  else
    echo "[ERROR] Could not find a boot overlays directory." >&2
    exit 1
  fi
  dest="${overlays}/spi0-4cs.dtbo"

  tmp="$(mktemp /tmp/spi0-4cs.XXXXXX.dtbo)"
  if ! dtc -@ -I dts -O dtb -o "$tmp" "$dts"; then
    rm -f "$tmp"
    echo "[ERROR] dtc failed to compile ${dts}" >&2
    exit 1
  fi
  if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
    rm -f "$tmp"
    log_success "SPI0 extra-CS overlay already installed: ${dest}"
    return 0
  fi
  sudo cp "$tmp" "$dest"
  rm -f "$tmp"
  CONFIG_CHANGED=1
  log_info "Installed ${dest}"
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
install_spi0_extra_cs_overlay
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
