#!/bin/bash
# LED Grid Animation System Deployment Script
# Deploys to Raspberry Pi with passwordless SSH

set -euo pipefail  # Exit on any error and fail on unset vars

# Configuration
PI_HOST="ledwallleft@ledwallleft.local"
DEPLOY_DIR="ledgrid-pod"
LOCAL_DIR="."
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
PI_USER="${PI_HOST%@*}"
SPI_SPEED="${SPI_SPEED:-10000000}"

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
        log_error "  3. Passwordless SSH is configured"
        log_error "  4. Hostname 'ledwallleft.local' resolves correctly"
        exit 1
    fi
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
    if ! ssh $SSH_OPTS "$PI_HOST" "sudo systemctl stop ledgrid.service 2>/dev/null || true; pkill -f start_server.py || true; pkill -f start.sh || true"; then
        log_warning "Stop step failed (likely nothing running); continuing..."
    fi
}

# Upload files to Pi (aggressive sync)
upload_files() {
    log_info "Uploading animation system files (full sync with delete)..."

    # Use rsync to mirror repo minus local-only artifacts
    rsync -az --delete --stats \
        -e "ssh $SSH_OPTS" \
        --exclude '.git' \
        --exclude 'venv' \
        --exclude 'test_venv' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.DS_Store' \
        --exclude 'animation_system.log' \
        --exclude '.pio' \
        --exclude '.pio/**' \
        --exclude 'build' \
        --exclude 'build/**' \
        --exclude '*/build' \
        --exclude '*/build/**' \
        --exclude 'dist' \
        --exclude 'dist/**' \
        --exclude '*/dist' \
        --exclude '*/dist/**' \
        --exclude 'out' \
        --exclude 'out/**' \
        --exclude '*/out' \
        --exclude '*/out/**' \
        "$LOCAL_DIR"/ "$PI_HOST:~/$DEPLOY_DIR/"

    log_success "File upload completed"
}

# Flash ESP32 firmware if sources changed (non-fatal on failure).
flash_esp32_firmware() {
    log_info "Checking ESP32 firmware..."
    if ssh $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && DEPLOY_DIR=~/$DEPLOY_DIR bash tools/deployment/flash_esp32.sh"; then
        log_success "ESP32 firmware check complete"
    else
        log_warning "ESP32 flash failed; continuing deployment"
    fi
}

# Create virtual environment and install dependencies
setup_venv_and_dependencies() {
    log_info "Setting up Python virtual environment..."

    # Create virtual environment
    ssh $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && python3 -m venv venv"

    log_success "Virtual environment created"

    log_info "Installing Python dependencies in venv..."

    # Install dependencies in virtual environment
    ssh $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"

    log_success "Dependencies installed in virtual environment"
}

# Check if SPI is enabled
check_spi() {
    log_info "Checking SPI configuration..."
    
    SPI_STATUS=$(ssh $SSH_OPTS "$PI_HOST" "ls /dev/spi* 2>/dev/null | wc -l || echo 0")
    
    if [ "$SPI_STATUS" -gt 0 ]; then
        log_success "SPI devices found: $(ssh $SSH_OPTS "$PI_HOST" "ls /dev/spi*")"
    else
        log_warning "No SPI devices found"
        log_warning "You may need to enable SPI with: sudo raspi-config"
        log_warning "Navigate to: Interface Options > SPI > Enable"
    fi
}

# Create startup script
create_startup_script() {
    log_info "Creating startup script..."

    ssh $SSH_OPTS "$PI_HOST" "cat > ~/$DEPLOY_DIR/start.sh << 'EOF'
#!/bin/bash
# LED Grid Animation System Startup Script

cd \$(dirname \$0)

echo \"🎨 Starting LED Grid Animation System...\"
echo \"📁 Working directory: \$(pwd)\"

# Check if virtual environment exists
if [ ! -d \"venv\" ]; then
    echo \"❌ Virtual environment not found!\"
    echo \"   Please run the deployment script again\"
    exit 1
fi

# Activate virtual environment
echo \"🐍 Activating virtual environment...\"
source venv/bin/activate

# Check if LED controller exists
if [ ! -f \"drivers/spi_controller.py\" ]; then
    echo \"⚠️  Warning: drivers/spi_controller.py not found\"
    echo \"   The system will run in demo mode\"
fi

# Get Pi's IP address for display
PI_IP=\$(hostname -I | awk '{print \$1}')

echo \"🌐 Starting web server...\"
echo \"   Local URL:  http://localhost:5000\"
echo \"   Network URL: http://\$PI_IP:5000\"
echo \"\"
echo \"🎮 Web Interface:\"
echo \"   Dashboard:    http://\$PI_IP:5000/\"
echo \"   Control Panel: http://\$PI_IP:5000/control\"
echo \"   Upload:       http://\$PI_IP:5000/upload\"
echo \"\"
echo \"Press Ctrl+C to stop\"
echo \"\"

DEFAULT_STRIPS=\$(python - <<'PY'
from drivers.led_layout import DEFAULT_STRIP_COUNT
print(DEFAULT_STRIP_COUNT)
PY
)
DEFAULT_LEDS_PER_STRIP=\$(python - <<'PY'
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP
print(DEFAULT_LEDS_PER_STRIP)
PY
)

STRIPS=\${STRIPS:-\$DEFAULT_STRIPS}
LEDS_PER_STRIP=\${LEDS_PER_STRIP:-\$DEFAULT_LEDS_PER_STRIP}
TARGET_FPS=\${TARGET_FPS:-150}
ANIMATION_SPEED_SCALE=\${ANIMATION_SPEED_SCALE:-0.2}
SPI_SPEED=\${SPI_SPEED:-10000000}
HOST=\${HOST:-0.0.0.0}
PORT=\${PORT:-5000}
CONTROL_FILE=\${CONTROL_FILE:-run_state/control.json}
STATUS_FILE=\${STATUS_FILE:-run_state/status.json}
ANIM_DIR=\${ANIM_DIR:-animation/plugins}
POLL_INTERVAL=\${POLL_INTERVAL:-0.5}
STATUS_INTERVAL=\${STATUS_INTERVAL:-0.5}
PYTHONUNBUFFERED=1
export PYTHONUNBUFFERED

mkdir -p \"\$(dirname \"\$CONTROL_FILE\")\" \"\$(dirname \"\$STATUS_FILE\")\"

echo \"🧭 Using control file: \$CONTROL_FILE\"
echo \"🧭 Using status file : \$STATUS_FILE\"
echo \"🧭 Animations dir   : \$ANIM_DIR\"
echo \"\"

# Start controller process
echo \"▶️  Starting controller (hardware) process...\"
nohup python scripts/start_server.py \\
    --mode controller \\
    --control-file \"\$CONTROL_FILE\" \\
    --status-file \"\$STATUS_FILE\" \\
    --animations-dir \"\$ANIM_DIR\" \\
    --strips \"\$STRIPS\" \\
    --leds-per-strip \"\$LEDS_PER_STRIP\" \\
    --target-fps \"\$TARGET_FPS\" \\
    --spi-speed \"\$SPI_SPEED\" \\
    --animation-speed-scale \"\$ANIMATION_SPEED_SCALE\" \\
    --poll-interval \"\$POLL_INTERVAL\" \\
    --status-interval \"\$STATUS_INTERVAL\" \\
    > controller.log 2>&1 &
echo \$! > run_state/controller.pid
echo \"    Controller PID: \$(cat run_state/controller.pid)\"

# Start web/preview process (same host/port as before; same-origin so no CORS issues)
echo \"🌐 Starting web/preview process...\"
nohup python scripts/start_server.py \\
    --mode web \\
    --control-file \"\$CONTROL_FILE\" \\
    --status-file \"\$STATUS_FILE\" \\
    --animations-dir \"\$ANIM_DIR\" \\
    --strips \"\$STRIPS\" \\
    --leds-per-strip \"\$LEDS_PER_STRIP\" \\
    --animation-speed-scale \"\$ANIMATION_SPEED_SCALE\" \\
    --host \"\$HOST\" \\
    --port \"\$PORT\" \\
    > web.log 2>&1 &
echo \$! > run_state/web.pid
echo \"    Web PID: \$(cat run_state/web.pid)\"

echo \"\"
echo \"Logs:\"
echo \"  Controller: controller.log\"
echo \"  Web UI    : web.log\"
EOF"

    # Make startup script executable
    ssh $SSH_OPTS "$PI_HOST" "chmod +x ~/$DEPLOY_DIR/start.sh"

    log_success "Startup script created"
}

create_systemd_service() {
    log_info "Installing systemd service..."

    if ssh $SSH_OPTS "$PI_HOST" "cat > /tmp/ledgrid.service << 'EOF'
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
Environment=SPI_SPEED=$SPI_SPEED

[Install]
WantedBy=multi-user.target
EOF
sudo mv /tmp/ledgrid.service /etc/systemd/system/ledgrid.service
sudo systemctl daemon-reload
sudo systemctl enable ledgrid.service"; then
        log_success "systemd service installed"
    else
        log_warning "systemd service install failed (check sudo permissions)"
    fi
}

# Start the animation system
start_system() {
    log_info "Starting LED Grid Animation System..."

    # Get Pi's IP address
    PI_IP=$(ssh $SSH_OPTS "$PI_HOST" "hostname -I | awk '{print \$1}'")
    
    log_success "🎉 Deployment completed successfully!"
    echo ""
    echo "🌐 LED Grid Animation System is now running at:"
    echo ""
    echo -e "${GREEN}   Dashboard:     http://$PI_IP:5000/${NC}"
    echo -e "${GREEN}   Control Panel: http://$PI_IP:5000/control${NC}"
    echo -e "${GREEN}   Upload:        http://$PI_IP:5000/upload${NC}"
    echo ""
    echo "🎮 You can now:"
    echo "   • View and start animations from the dashboard"
    echo "   • Upload new animation plugins"
    echo "   • Control animations in real-time"
    echo ""
    
    # Start the system via systemd when available.
    log_info "Restarting systemd service..."
    if ssh $SSH_OPTS "$PI_HOST" "sudo systemctl restart ledgrid.service"; then
        log_success "systemd restart issued"
    else
        log_warning "systemd restart failed; falling back to manual start.sh"
        ssh -f -n $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && SPI_SPEED=$SPI_SPEED nohup ./start.sh > animation_system.log 2>&1 </dev/null &"
    fi

    # Wait a moment for startup
    sleep 2
    
    # Check if it's running (non-fatal)
    if ssh $SSH_OPTS "$PI_HOST" "pgrep -f 'start_server.py' > /dev/null"; then
        log_success "Animation system is running!"
        echo ""
        echo -e "${BLUE}📊 System Status:${NC}"
        echo "   ✅ Animation server: Running"
        echo "   ✅ Web interface: Available"
        echo "   ✅ Plugin system: Ready"
        echo ""
        echo -e "${YELLOW}🚀 Open your browser and go to: http://$PI_IP:5000/${NC}"
    else
        log_warning "System may still be starting up..."
        echo "Check the log with: ssh $PI_HOST 'cd $DEPLOY_DIR && tail -f animation_system.log'"
    fi
}

# Tail logs to monitor startup
tail_logs() {
    echo ""
    echo -e "${BLUE}📋 Monitoring logs (Ctrl+C to exit)...${NC}"
    echo "========================================"
    echo ""
    
    # Give processes a moment to start writing logs
    sleep 1
    
    # Show initial log state
    echo -e "${GREEN}=== Web Server Log (last 10 lines) ===${NC}"
    ssh $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && tail -10 web.log 2>/dev/null || echo 'No web.log yet'"
    echo ""
    echo -e "${GREEN}=== Controller Log (last 10 lines) ===${NC}"
    ssh $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && tail -10 controller.log 2>/dev/null || echo 'No controller.log yet'"
    echo ""
    echo -e "${BLUE}=== Following logs (press Ctrl+C to stop) ===${NC}"
    echo ""
    
    # Tail both logs simultaneously
    ssh $SSH_OPTS "$PI_HOST" "cd ~/$DEPLOY_DIR && tail -f web.log controller.log"
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
    if [ -z "${SKIP_FIRMWARE:-}" ]; then
        flash_esp32_firmware
    else
        log_warning "Skipping ESP32 firmware flash (SKIP_FIRMWARE set)"
    fi
    setup_venv_and_dependencies
    check_spi
    create_startup_script
    create_systemd_service
    start_system
    tail_logs
}

# Run main function
main "$@"
