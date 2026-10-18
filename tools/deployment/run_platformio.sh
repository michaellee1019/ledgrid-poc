#!/usr/bin/env bash
# Run PlatformIO with a Python version compatible with ~/.platformio/penv.
# ``uv run --with platformio`` often uses Python 3.13, which makes PlatformIO
# recreate its penv and can break espressif32 (littlefs import errors).

set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: run_platformio.sh <pio-args...>" >&2
  exit 2
fi

if command -v pio >/dev/null 2>&1 && pio --version >/dev/null 2>&1; then
  exec pio "$@"
fi

if [ -x "${HOME}/.platformio/penv/bin/pio" ]; then
  exec "${HOME}/.platformio/penv/bin/pio" "$@"
fi

if command -v uv >/dev/null 2>&1; then
  exec uv run --python 3.12 --with platformio pio "$@"
fi

echo "PlatformIO not found. Install with: pip install platformio" >&2
echo "or: uv run --python 3.12 --with platformio pio --version" >&2
exit 127
