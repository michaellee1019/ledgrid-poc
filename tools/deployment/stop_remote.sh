#!/usr/bin/env bash
# Operate the installed wall service on its Raspberry Pi.
set -euo pipefail

action=${1:-stop}
case "$action" in
    stop|restart) remote_command="sudo -n systemctl $action ledgrid.service" ;;
    status) remote_command="systemctl status --no-pager ledgrid.service" ;;
    *) echo "Usage: $0 [stop|status|restart]" >&2; exit 2 ;;
esac

# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"
exec ssh "${SSH_OPTS[@]}" "$PI_HOST" "$remote_command"
