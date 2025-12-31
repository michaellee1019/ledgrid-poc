#!/bin/bash
# Stop LED Grid Animation System on Raspberry Pi

set -e

# Configuration
PI_HOST="ledwallleft@ledwallleft.local"
DEPLOY_DIR="ledgrid-pod"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# Stop the animation system
stop_system() {
    log_info "Stopping LED Grid Animation System on $PI_HOST..."
    
    # Check if system is running
    if ssh "$PI_HOST" "pgrep -f 'start_server.py' > /dev/null"; then
        log_info "Found running animation server, stopping..."
        
        # Stop the process
        ssh "$PI_HOST" "pkill -f 'start_server.py' || true"
        
        # Wait a moment
        sleep 2
        
        # Check if stopped
        if ! ssh "$PI_HOST" "pgrep -f 'start_server.py' > /dev/null"; then
            log_success "Animation system stopped successfully"
        else
            log_warning "Process may still be running, trying force stop..."
            ssh "$PI_HOST" "pkill -9 -f 'start_server.py' || true"
            sleep 1
            
            if ! ssh "$PI_HOST" "pgrep -f 'start_server.py' > /dev/null"; then
                log_success "Animation system force stopped"
            else
                log_error "Failed to stop animation system"
                exit 1
            fi
        fi
    else
        log_warning "Animation system is not running"
    fi
}

# Show system status
show_status() {
    log_info "Checking system status..."
    
    if ssh "$PI_HOST" "pgrep -f 'start_server.py' > /dev/null"; then
        PI_IP=$(ssh "$PI_HOST" "hostname -I | awk '{print \$1}'")
        echo -e "${GREEN}Status: RUNNING${NC}"
        echo "Web interface: http://$PI_IP:5000/"
    else
        echo -e "${RED}Status: STOPPED${NC}"
    fi
    
    # Show recent log entries if available
    if ssh "$PI_HOST" "[ -f ~/$DEPLOY_DIR/animation_system.log ]"; then
        echo ""
        log_info "Recent log entries:"
        ssh "$PI_HOST" "tail -5 ~/$DEPLOY_DIR/animation_system.log" || true
    fi
}

# Main function
main() {
    case "${1:-stop}" in
        "stop")
            echo "🛑 Stopping LED Grid Animation System"
            echo "====================================="
            stop_system
            ;;
        "status")
            echo "📊 LED Grid Animation System Status"
            echo "==================================="
            show_status
            ;;
        "restart")
            echo "🔄 Restarting LED Grid Animation System"
            echo "======================================="
            stop_system
            sleep 2
            log_info "Starting system with virtual environment..."
            ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && nohup ./start.sh > animation_system.log 2>&1 &"
            sleep 3
            show_status
            ;;
        *)
            echo "Usage: $0 [stop|status|restart]"
            echo ""
            echo "Commands:"
            echo "  stop     - Stop the animation system (default)"
            echo "  status   - Show current status"
            echo "  restart  - Stop and restart the system"
            exit 1
            ;;
    esac
}

main "$@"
