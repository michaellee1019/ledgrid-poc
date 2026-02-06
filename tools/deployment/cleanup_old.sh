#!/bin/bash
# Cleanup old LED Grid Animation System files and processes
# Run this on the Pi if upgrading from old version

set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-$HOME/ledgrid-pod}"

echo "🧹 Cleaning up old LED Grid files..."

# Stop any old processes
echo "Stopping old processes..."
sudo systemctl stop ledgrid.service 2>/dev/null || true
pkill -f start_animation_server.py 2>/dev/null || true
pkill -f start_server.py 2>/dev/null || true
pkill -f start.sh 2>/dev/null || true
pkill -f start_systemd.sh 2>/dev/null || true

sleep 2

# Remove old files
if [ -f "$DEPLOY_DIR/start_animation_server.py" ]; then
    echo "Removing old start_animation_server.py..."
    rm -f "$DEPLOY_DIR/start_animation_server.py"
fi

if [ -f "$DEPLOY_DIR/animation_manager.py" ]; then
    echo "Removing old animation_manager.py..."
    rm -f "$DEPLOY_DIR/animation_manager.py"
fi

# Remove old PID files
rm -f "$DEPLOY_DIR/run_state/"*.pid 2>/dev/null || true

# Remove old log references
if [ -f "$DEPLOY_DIR/animation_system.log" ]; then
    echo "Archiving old animation_system.log..."
    mv "$DEPLOY_DIR/animation_system.log" "$DEPLOY_DIR/animation_system.log.old" 2>/dev/null || true
fi

echo "✅ Cleanup complete!"
echo ""
echo "Now run deployment again:"
echo "  just deploy"

