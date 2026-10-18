#!/usr/bin/env bash
# Configure passwordless sudo on the deploy target when missing (one interactive prompt).

set -euo pipefail

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"

ensure_remote_passwordless_sudo
