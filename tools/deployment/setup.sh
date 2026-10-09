#!/bin/bash
# One-time runtime and firmware tooling on the installed Pi.
set -euo pipefail
source "$(dirname "$0")/ssh_helpers.sh"
ssh "${SSH_ARGS[@]}" "$PI_HOST" 'bash -se' <<'REMOTE'
set -euo pipefail
sudo -n true
sudo -n apt-get update
sudo -n apt-get install -y python3-venv python3-dev build-essential ccache
python3 -m venv "$HOME/.platformio-venv"
"$HOME/.platformio-venv/bin/python" -m pip install platformio==6.1.19
sudo -n usermod -aG dialout "$USER"
echo 'Setup complete. Reconnect if serial group membership changed.'
REMOTE
