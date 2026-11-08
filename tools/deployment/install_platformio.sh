#!/bin/bash
# Install or repair PlatformIO Core on the deploy target (run on the Pi).
#
# PlatformIO 6.2.0 pulls tool-scons ~4.41101.0, which races the pioarduino
# platform's bundled scons-4.8.1 package and breaks ESP-IDF builds on Python
# 3.13 (ModuleNotFoundError: SCons.Tool.FortranCommon). Pin Core 6.1.19 and
# tool-scons 4.40801.0 to match requirements-platformio.lock and platform 55.03.39.

set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

log_info() { echo "[INFO] $1"; }
log_success() { echo "[SUCCESS] $1"; }

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "[ERROR] python3-venv is required; run just setup from a machine with sudo on the Pi"
  exit 1
fi

VENV_DIR="${PLATFORMIO_VENV:-$HOME/.platformio-venv}"
log_info "Ensuring PlatformIO venv at $VENV_DIR"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install --upgrade 'platformio==6.1.19'

mkdir -p "$HOME/.local/bin"
ln -sf "$VENV_DIR/bin/pio" "$HOME/.local/bin/pio"

if [ -x "$HOME/.platformio/penv/bin/python" ]; then
  log_info "Aligning PlatformIO Core in ~/.platformio/penv with 6.1.19"
  "$HOME/.platformio/penv/bin/python" -m pip install --upgrade 'platformio==6.1.19' || true
fi

log_info "Installing pinned tool-scons (4.40801.0 / SCons 4.8.1)"
"$VENV_DIR/bin/pio" pkg install -g -t platformio/tool-scons@4.40801.0

if ! "$VENV_DIR/bin/pio" --version >/dev/null 2>&1; then
  echo "[ERROR] PlatformIO install failed"
  exit 1
fi

log_success "PlatformIO $( "$VENV_DIR/bin/pio" --version ) ready"
