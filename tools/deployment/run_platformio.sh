#!/usr/bin/env bash
# Run PlatformIO via the newest compatible Core (avoids stale PATH shims and uv/py3.13).

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: run_platformio.sh <pio-args...>" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${script_dir}/platformio_cli.py" "$@"
