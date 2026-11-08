#!/usr/bin/env bash
# Verify the deploy target is provisioned for non-interactive sudo.

set -euo pipefail

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"

if ! remote_has_passwordless_sudo; then
    echo "Cannot verify non-interactive sudo on $PI_HOST. Check SSH and run just setup before deployment." >&2
    exit 1
fi
