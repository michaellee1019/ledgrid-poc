#!/usr/bin/env bash
# Collect systemd logs and current wall telemetry.
set -euo pipefail

OUT_FILE=${OUT_FILE-diagnostics/remote_diagnostics.out}
if [ -n "$OUT_FILE" ]; then
    mkdir -p "$(dirname "$OUT_FILE")"
    exec >"$OUT_FILE" 2>&1
fi

deployment_tools="$(cd "$(dirname "$0")/../deployment" && pwd)"
PI_HOST=${REMOTE:-${PI_HOST:-}}
# shellcheck source=../deployment/ssh_helpers.sh
source "$deployment_tools/ssh_helpers.sh"

restart=${RESTART_WEB:-0}
if [[ "$restart" != 0 && "$restart" != 1 ]]; then
    echo "RESTART_WEB must be 0 or 1" >&2
    exit 2
fi
port=${PORT:-}
if [[ -n "$port" && ! "$port" =~ ^[0-9]+$ ]]; then
    echo "PORT must be numeric" >&2
    exit 2
fi

ssh "${SSH_OPTS[@]}" "$PI_HOST" "bash -s -- $restart '$port'" <<'REMOTE'
set -euo pipefail

if [ "$1" = 1 ]; then
    sudo -n systemctl restart ledgrid.service
fi

echo "=== Service"
systemctl status --no-pager ledgrid.service || true

echo "=== Service journal"
journalctl -u ledgrid.service -n 60 --no-pager

working_dir=$(systemctl show ledgrid.service --property=WorkingDirectory --value)
if [ -n "$working_dir" ]; then
    for name in controller.log web.log; do
        echo "=== $name"
        if [ -f "$working_dir/$name" ]; then
            tail -n 60 "$working_dir/$name"
        else
            echo "Log unavailable"
        fi
    done
fi

port=$2
if [ -z "$port" ]; then
    port=$(systemctl show ledgrid.service --property=Environment --value | python3 -c '
import shlex
import sys

env = dict(item.split("=", 1) for item in shlex.split(sys.stdin.read()) if "=" in item)
print(env.get("PORT", "5000"))
')
fi
if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    echo "Invalid ledgrid.service port" >&2
    exit 2
fi

echo "=== Composer operations telemetry"
url="http://127.0.0.1:$port/api/v1/composer/operations/telemetry"
if [ "$1" = 1 ]; then
    curl --fail --silent --show-error --retry 5 --retry-connrefused --max-time 2 "$url"
else
    curl --fail --silent --show-error --max-time 2 "$url"
fi
REMOTE
