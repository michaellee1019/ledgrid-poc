#!/bin/bash
# LED Grid Animation System Deployment Script
# Deploys to Raspberry Pi with passwordless SSH

set -euo pipefail  # Exit on any error and fail on unset vars

# Configuration
PI_HOST="${PI_HOST:-ledgridwall@ledgridwall.local}"
DEPLOY_DIR="${DEPLOY_DIR:-ledgrid-pod}"
LOCAL_DIR="${LOCAL_DIR:-.}"
# shellcheck source=ssh_helpers.sh
source "$(dirname "$0")/ssh_helpers.sh"
# shellcheck source=sync_files.sh
source "$(dirname "$0")/sync_files.sh"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if SSH connection works
check_ssh_connection() {
    log_info "Testing SSH connection to $PI_HOST..."
    if ssh $SSH_OPTS "$PI_HOST" "echo 'SSH connection successful'" >/dev/null 2>&1; then
        log_success "SSH connection to $PI_HOST is working"
    else
        log_error "Cannot connect to $PI_HOST via SSH"
        log_error "Please ensure:"
        log_error "  1. Raspberry Pi is powered on and connected to network"
        log_error "  2. SSH is enabled on the Pi"
        log_error "  3. Passwordless SSH is configured (ssh-copy-id)"
        log_error "  4. Hostname 'ledgridwall.local' resolves correctly"
        exit 1
    fi

    ensure_remote_passwordless_sudo
}

# Create deployment directory on Pi
create_deploy_directory() {
    log_info "Creating deployment directory: ~/$DEPLOY_DIR"
    ssh $SSH_OPTS "$PI_HOST" "mkdir -p ~/$DEPLOY_DIR"
    log_success "Deployment directory created"
}

# Stop any running instances on the Pi
stop_running() {
    log_info "Stopping any running animation server on the Pi..."
    if ! ssh $SSH_OPTS "$PI_HOST" "sudo systemctl stop ledgrid.service 2>/dev/null || true; pkill -f start_server.py || true; pkill -f start_systemd.sh || true"; then
        log_warning "Stop step failed (likely nothing running); continuing..."
    fi
}

# Upload tracked files to Pi while preserving target-owned state.
upload_files() {
    log_info "Uploading tracked files (full sync with protected runtime state)..."
    sync_full_deployment

    log_success "File upload completed"
}

# Flash ESP32 firmware if sources changed.
flash_esp32_firmware() {
    log_info "Checking ESP32 firmware..."
    ssh $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && DEPLOY_DIR=~/$DEPLOY_DIR DEBUG=${DEBUG:-0} bash tools/deployment/flash_esp32.sh"
    log_success "ESP32 firmware check complete"
}

# Ensure /boot/firmware/config.txt has the correct SPI overlays (idempotent).
configure_remote_spi() {
    log_info "Configuring Raspberry Pi SPI boot settings..."
    ensure_remote_spi
}

# Create virtual environment and install dependencies
setup_venv_and_dependencies() {
    log_info "Setting up Python virtual environment..."

    log_info "Checking Python build dependencies..."
    ssh $SSH_OPTS "$PI_HOST" "bash -s" <<'EOF'
set -euo pipefail
missing=()
for package in python3-dev build-essential git; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'ok installed' || missing+=("$package")
done
if [ "${#missing[@]}" -gt 0 ]; then
  sudo apt-get update
  sudo apt-get install -y "${missing[@]}"
fi
EOF

    log_info "Checking Python virtual environment and dependency hash..."
    ssh $SSH_OPTS "$PI_HOST" "bash -s -- '$DEPLOY_DIR'" <<'EOF'
set -euo pipefail
deploy_dir=$1
cd ~/"$deploy_dir"
created=0
if [ ! -x venv/bin/python ]; then
  python3 -m venv venv
  created=1
fi
requirements_hash=$(sha256sum requirements.txt | awk '{print $1}')
installed_hash=$(cat venv/.ledgrid_requirements_sha256 2>/dev/null || true)
if [ "$created" = 1 ] || [ "$requirements_hash" != "$installed_hash" ]; then
  if [ "$created" = 1 ]; then venv/bin/python -m pip install --upgrade pip; fi
  venv/bin/python -m pip install -r requirements.txt
  printf '%s\n' "$requirements_hash" > venv/.ledgrid_requirements_sha256
else
  echo "[INFO] Python dependencies unchanged; skipping pip install"
fi
EOF

    log_success "Python environment is ready"
}

# Check if SPI is enabled
check_spi() {
    log_info "Checking SPI configuration..."

    if remote_spi_devices_ready >/dev/null 2>&1; then
        log_success "SPI devices found:"
        remote_spi_devices_ready
    else
        log_warning "Required SPI devices are missing"
        log_warning "Wall layout expects: /dev/spidev0.0 /dev/spidev0.1 /dev/spidev1.0 /dev/spidev1.1"
        log_warning "Re-run deploy to configure SPI and reboot the Pi"
    fi
}

create_systemd_service() {
    log_info "Installing systemd service..."

    ssh $SSH_OPTS "$PI_HOST" "cat > /tmp/ledgrid.service << 'EOF'
[Unit]
Description=LED Grid Animation System
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$PI_USER
WorkingDirectory=/home/$PI_USER/$DEPLOY_DIR
ExecStart=/bin/bash /home/$PI_USER/$DEPLOY_DIR/scripts/start_systemd.sh
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1
Environment=LEDGRID_SPI1_MODE=0
Environment=LEDGRID_HAT=0
Environment=STRIPS=32

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/ledgrid.service /etc/systemd/system/ledgrid.service
sudo systemctl daemon-reload
sudo systemctl enable ledgrid.service"
    log_success "systemd service installed"
}

# Start the animation system
start_system() {
    log_info "Restarting systemd service..."
    if ssh $SSH_OPTS "$PI_HOST" "sudo systemctl restart ledgrid.service"; then
        log_success "systemd restart issued"
    else
        log_warning "systemd restart failed; falling back to the tracked startup script"
        ssh -f -n $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && nohup ./scripts/start_systemd.sh > animation_system.log 2>&1 </dev/null &"
    fi
}

# Verify the web process reached a usable state instead of leaving deploy
# attached to an unbounded log tail.
check_web_server() {
    log_info "Checking web server..."
    if ! ssh $SSH_OPTS "$PI_HOST" \
        "for attempt in {1..120}; do curl --fail --silent --max-time 2 http://127.0.0.1:5000/api/status >/dev/null && exit 0; sleep 0.25; done; exit 1"; then
        log_error "Web server did not become healthy; collecting startup logs"
        ssh $SSH_OPTS "$PI_HOST" \
            "sudo systemctl status ledgrid.service --no-pager -l || true; tail -80 ~/$DEPLOY_DIR/web.log 2>/dev/null || true; tail -80 ~/$DEPLOY_DIR/controller.log 2>/dev/null || true"
        return 1
    fi
    log_success "Web server is responding"
}

# Main deployment process
main() {
    echo "🚀 LED Grid Animation System Deployment"
    echo "========================================"
    echo ""
    
    check_ssh_connection
    create_deploy_directory
    stop_running
    upload_files
    configure_remote_spi
    if [ -z "${SKIP_FIRMWARE:-}" ]; then
        flash_esp32_firmware
    else
        log_warning "Skipping ESP32 firmware flash (SKIP_FIRMWARE set)"
    fi
    setup_venv_and_dependencies
    check_spi
    create_systemd_service
    start_system
    check_web_server
    log_success "Deployment complete: http://${PI_HOST#*@}:5000/"
}

# Run main function
main "$@"
