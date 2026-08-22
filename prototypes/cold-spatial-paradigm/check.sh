#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

node --check "$SCRIPT_DIR/app.js"
node "$SCRIPT_DIR/check.mjs"
