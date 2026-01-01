#!/bin/bash
# Virtual Environment Management for LED Grid Animation System

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

# Check virtual environment status
check_venv() {
    log_info "Checking virtual environment status..."
    
    if ssh "$PI_HOST" "[ -d ~/$DEPLOY_DIR/venv ]"; then
        log_success "Virtual environment exists"
        
        # Check if it's working
        if ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && python --version"; then
            log_success "Virtual environment is functional"
            
            # Show installed packages
            echo ""
            log_info "Installed packages:"
            ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && pip list"
        else
            log_error "Virtual environment exists but is not functional"
        fi
    else
        log_warning "Virtual environment does not exist"
    fi
}

# Recreate virtual environment
recreate_venv() {
    log_info "Recreating virtual environment..."
    
    # Remove existing venv if it exists
    ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && rm -rf venv"
    
    # Create new virtual environment
    ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && python3 -m venv venv"
    
    log_success "Virtual environment created"
    
    # Install dependencies
    log_info "Installing dependencies..."
    ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"
    
    log_success "Dependencies installed"
}

# Install additional package
install_package() {
    local package="$1"
    if [ -z "$package" ]; then
        log_error "Package name required"
        echo "Usage: $0 install <package_name>"
        exit 1
    fi
    
    log_info "Installing package: $package"
    
    if ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && pip install '$package'"; then
        log_success "Package '$package' installed successfully"
    else
        log_error "Failed to install package '$package'"
        exit 1
    fi
}

# Update all packages
update_packages() {
    log_info "Updating all packages..."
    
    ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && pip install --upgrade pip && pip install --upgrade -r requirements.txt"
    
    log_success "Packages updated"
}

# Show virtual environment info
show_info() {
    log_info "Virtual Environment Information"
    echo "==============================="
    
    if ssh "$PI_HOST" "[ -d ~/$DEPLOY_DIR/venv ]"; then
        echo ""
        echo "📍 Location: ~/$DEPLOY_DIR/venv"
        
        echo ""
        log_info "Python version:"
        ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && python --version"
        
        echo ""
        log_info "Pip version:"
        ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && pip --version"
        
        echo ""
        log_info "Virtual environment size:"
        ssh "$PI_HOST" "du -sh ~/$DEPLOY_DIR/venv"
        
        echo ""
        log_info "Installed packages:"
        ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && pip list --format=columns"
        
        echo ""
        log_info "Requirements file:"
        ssh "$PI_HOST" "cd ~/$DEPLOY_DIR && cat requirements.txt"
    else
        log_error "Virtual environment does not exist"
        echo "Run: $0 recreate"
    fi
}

# Main function
main() {
    case "${1:-status}" in
        "status"|"check")
            echo "🔍 Checking Virtual Environment Status"
            echo "======================================"
            check_venv
            ;;
        "recreate"|"rebuild")
            echo "🔄 Recreating Virtual Environment"
            echo "================================="
            recreate_venv
            ;;
        "install")
            echo "📦 Installing Package"
            echo "===================="
            install_package "$2"
            ;;
        "update")
            echo "⬆️  Updating Packages"
            echo "===================="
            update_packages
            ;;
        "info")
            echo "ℹ️  Virtual Environment Information"
            show_info
            ;;
        "shell")
            echo "🐚 Opening Shell in Virtual Environment"
            echo "======================================"
            log_info "Connecting to Pi with virtual environment activated..."
            ssh -t "$PI_HOST" "cd ~/$DEPLOY_DIR && source venv/bin/activate && bash"
            ;;
        *)
            echo "Virtual Environment Management for LED Grid Animation System"
            echo "==========================================================="
            echo ""
            echo "Usage: $0 [command] [options]"
            echo ""
            echo "Commands:"
            echo "  status     - Check virtual environment status (default)"
            echo "  recreate   - Recreate virtual environment and install dependencies"
            echo "  install    - Install additional package (e.g., $0 install numpy)"
            echo "  update     - Update all packages to latest versions"
            echo "  info       - Show detailed virtual environment information"
            echo "  shell      - Open interactive shell with venv activated"
            echo ""
            echo "Examples:"
            echo "  $0 status                    # Check if venv is working"
            echo "  $0 recreate                  # Fix broken venv"
            echo "  $0 install numpy             # Add numpy package"
            echo "  $0 update                    # Update all packages"
            echo "  $0 shell                     # Interactive debugging"
            exit 1
            ;;
    esac
}

main "$@"
