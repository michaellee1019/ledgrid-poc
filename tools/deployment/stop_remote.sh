#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/ssh_helpers.sh"
operation="${1:-stop}"
case "$operation" in
    stop|restart) ssh "${SSH_ARGS[@]}" "$PI_HOST" "sudo -n systemctl $operation ledgrid.service" ;;
    status) ssh "${SSH_ARGS[@]}" "$PI_HOST" 'systemctl status ledgrid.service --no-pager' ;;
    *) echo "Usage: $0 stop|restart|status" >&2; exit 1 ;;
esac
