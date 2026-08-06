#!/bin/bash
# Compatibility wrapper. Wall-authored masks and presets are one fetch operation.

set -euo pipefail

exec "$(dirname "$0")/fetch_wall_data.sh" "$@"
