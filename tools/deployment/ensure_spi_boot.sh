#!/usr/bin/env bash
# Apply SPI boot overlays on the deploy target (may reboot the Pi).

set -euo pipefail

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"

ensure_remote_spi
