# Shared SSH/sudo helpers for remote deployment.
# Source after setting PI_HOST if you need a non-default host.

PI_HOST="${PI_HOST:-ledgridwall@ledgridwall.local}"
PI_USER="${PI_USER:-${PI_HOST%@*}}"
SSH_OPTS="${SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new}"
SSH_OPTS_TTY="${SSH_OPTS_TTY:--o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new}"

_deploy_log_info() {
    if declare -F log_info >/dev/null 2>&1; then
        log_info "$1"
    else
        echo "[INFO] $1"
    fi
}

_deploy_log_success() {
    if declare -F log_success >/dev/null 2>&1; then
        log_success "$1"
    else
        echo "[SUCCESS] $1"
    fi
}

_deploy_log_error() {
    if declare -F log_error >/dev/null 2>&1; then
        log_error "$1"
    else
        echo "[ERROR] $1" >&2
    fi
}

_deploy_log_warning() {
    if declare -F log_warning >/dev/null 2>&1; then
        log_warning "$1"
    else
        echo "[WARNING] $1"
    fi
}

remote_has_passwordless_sudo() {
    ssh $SSH_OPTS "$PI_HOST" "sudo -n true" >/dev/null 2>&1
}

ensure_remote_passwordless_sudo() {
    if remote_has_passwordless_sudo; then
        return 0
    fi

    _deploy_log_info "Passwordless sudo is not configured on $PI_HOST"
    _deploy_log_info "SSH keys handle login; sudo still needs your Pi password once on a fresh install"
    _deploy_log_info "You will be prompted for your Pi user password now..."

    if ! ssh -t $SSH_OPTS_TTY "$PI_HOST" "bash -s" <<EOF
set -euo pipefail
SUDOERS_FILE="/etc/sudoers.d/ledgrid-deploy"
TMP="\$(mktemp)"
trap 'rm -f "\$TMP"' EXIT
echo "${PI_USER} ALL=(ALL) NOPASSWD: ALL" > "\$TMP"
sudo install -m 440 -o root -g root "\$TMP" "\$SUDOERS_FILE"
sudo visudo -c -f "\$SUDOERS_FILE"
echo "[SUCCESS] Passwordless sudo configured for \$(whoami)"
EOF
    then
        _deploy_log_error "Failed to configure passwordless sudo on $PI_HOST"
        _deploy_log_error "Run manually on the Pi:"
        _deploy_log_error "  echo '${PI_USER} ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/ledgrid-deploy"
        _deploy_log_error "  sudo chmod 440 /etc/sudoers.d/ledgrid-deploy"
        return 1
    fi

    if ! remote_has_passwordless_sudo; then
        _deploy_log_error "Passwordless sudo still not working after setup"
        return 1
    fi

    _deploy_log_success "Passwordless sudo is configured for $PI_USER"
}

wait_for_remote_ssh() {
    local max_attempts="${1:-60}"
    local sleep_seconds="${2:-2}"
    local attempt=0

    _deploy_log_info "Waiting for $PI_HOST to come back online..."
    sleep 5

    while [ "$attempt" -lt "$max_attempts" ]; do
        if ssh $SSH_OPTS "$PI_HOST" "echo ok" >/dev/null 2>&1; then
            sleep 3
            return 0
        fi
        sleep "$sleep_seconds"
        attempt=$((attempt + 1))
    done

    _deploy_log_error "Timed out waiting for $PI_HOST after reboot"
    return 1
}

remote_spi_devices_ready() {
    ssh $SSH_OPTS "$PI_HOST" "bash -s" <<'EOF'
set -euo pipefail
is_hat() {
  case "${LEDGRID_HAT:-0}" in
    1|true|TRUE|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}
if is_hat; then
  required=(/dev/spidev0.0 /dev/spidev0.1)
else
  required=(/dev/spidev0.0 /dev/spidev0.1 /dev/spidev1.0 /dev/spidev1.1)
fi
for dev in "${required[@]}"; do
  if [ ! -e "$dev" ]; then
    exit 1
  fi
done
ls -l "${required[@]}"
EOF
}

ensure_remote_spi() {
    local deploy_dir="${DEPLOY_DIR:-ledgrid-pod}"
    local configure_script="/home/${PI_USER}/${deploy_dir}/tools/deployment/configure_spi.sh"
    local output status config_changed

    _deploy_log_info "Ensuring boot SPI config (/boot/firmware/config.txt) and device nodes..."

    if ! output=$(ssh $SSH_OPTS "$PI_HOST" "LEDGRID_HAT=${LEDGRID_HAT:-0} bash ${configure_script}" 2>&1); then
        _deploy_log_error "SPI configuration failed"
        echo "$output" >&2
        return 1
    fi

    echo "$output"

    status=$(echo "$output" | awk -F= '/^STATUS=/{print $2; exit}')
    config_changed=$(echo "$output" | awk -F= '/^CONFIG_CHANGED=/{print $2; exit}')

    if [ "$status" = "ready" ]; then
        _deploy_log_success "SPI boot config and device nodes are ready on $PI_HOST"
        return 0
    fi

    if [ "${SKIP_SPI_REBOOT:-}" = "1" ]; then
        _deploy_log_warning "SPI not fully ready; skipping reboot (SKIP_SPI_REBOOT=1)"
        if [ "$config_changed" = "1" ]; then
            _deploy_log_warning "Boot config was updated — reboot the Pi manually before starting the controller"
        fi
        return 0
    fi

    if [ "$config_changed" = "1" ]; then
        _deploy_log_info "Boot config updated. Rebooting $PI_HOST..."
    else
        _deploy_log_info "SPI device nodes missing. Rebooting $PI_HOST..."
    fi
    ssh $SSH_OPTS "$PI_HOST" "sudo reboot" >/dev/null 2>&1 || true

    if ! wait_for_remote_ssh; then
        return 1
    fi

    if remote_spi_devices_ready >/dev/null 2>&1; then
        _deploy_log_success "SPI devices ready after reboot"
        remote_spi_devices_ready
        return 0
    fi

    _deploy_log_error "Required SPI devices still missing after reboot"
    _deploy_log_error "Expected: /dev/spidev0.0 /dev/spidev0.1 /dev/spidev1.0 /dev/spidev1.1"
    _deploy_log_error "Check boot config on the Pi and run deploy again"
    return 1
}
