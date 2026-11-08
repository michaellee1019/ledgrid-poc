# Shared SSH/sudo helpers for remote deployment.
# Source after setting PI_HOST if you need a non-default host.

PI_HOST="${PI_HOST:-ledgridwall5@ledgridwall5.local}"
_deployment_helper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
wall_ssh_options=$(python3 "$_deployment_helper_dir/deploy_ssh.py") || exit 1
if [[ "$PI_HOST" == -* ]]; then
    echo "Invalid SSH target" >&2
    exit 1
fi
SSH_OPTS=()
while IFS= read -r option; do
    SSH_OPTS+=("$option")
done <<< "$wall_ssh_options"

validate_deploy_dir() {
    local dir=$1
    if [[ ! "$dir" =~ ^[[:alnum:]_.-]+(/[[:alnum:]_.-]+)*$ ]] || [[ "/$dir/" == *"/../"* || "/$dir/" == *"/./"* ]]; then
        echo "DEPLOY_DIR must be a named directory below the target home" >&2
        return 1
    fi
}

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

remote_has_passwordless_sudo() {
    ssh "${SSH_OPTS[@]}" "$PI_HOST" "sudo -n true" >/dev/null 2>&1
}

ensure_remote_passwordless_sudo() {
    if remote_has_passwordless_sudo; then
        return 0
    fi

    _deploy_log_info "Passwordless sudo is not configured on $PI_HOST"
    _deploy_log_info "Setup will ask for the Pi account's sudo password"

    # Pass the setup command as an argument so the interactive sudo prompt
    # receives input from the workstation terminal.
    if ! ssh -t "${SSH_OPTS[@]}" "$PI_HOST" 'set -eu
        tmp=$(mktemp)
        trap "rm -f -- \"$tmp\"" EXIT
        printf "%s ALL=(ALL) NOPASSWD: ALL\n" "$(id -un)" > "$tmp"
        sudo visudo -c -f "$tmp"
        sudo install -m 440 -o root -g root "$tmp" /etc/sudoers.d/ledgrid-deploy'
    then
        _deploy_log_error "Failed to configure passwordless sudo on $PI_HOST"
        return 1
    fi

    if ! remote_has_passwordless_sudo; then
        _deploy_log_error "Passwordless sudo still not working after setup"
        return 1
    fi

    _deploy_log_success "Passwordless sudo is configured on $PI_HOST"
}

wait_for_remote_ssh() {
    local max_attempts="${1:-60}"
    local sleep_seconds="${2:-2}"
    local attempt=0

    _deploy_log_info "Waiting for $PI_HOST to come back online..."
    sleep 5

    while [ "$attempt" -lt "$max_attempts" ]; do
        if ssh "${SSH_OPTS[@]}" "$PI_HOST" "echo ok" >/dev/null 2>&1; then
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
    ssh "${SSH_OPTS[@]}" "$PI_HOST" "bash -s" <<'EOF'
set -euo pipefail
required=(/dev/spidev0.0 /dev/spidev0.1 /dev/spidev0.3 /dev/spidev1.0 /dev/spidev1.1)
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
    local output status config_changed
    local quoted_dir
    validate_deploy_dir "$deploy_dir" || return 1
    printf -v quoted_dir '%q' "$deploy_dir"

    _deploy_log_info "Ensuring boot SPI config (/boot/firmware/config.txt) and device nodes..."

    if ! output=$(ssh "${SSH_OPTS[@]}" "$PI_HOST" "bash -s" 2>&1 <<EOF
set -euo pipefail
deploy_dir=${quoted_dir}
for candidate in \
  "\$HOME/\${deploy_dir}/incoming/tools/deployment/configure_spi.sh" \
  "\$HOME/\${deploy_dir}/app/tools/deployment/configure_spi.sh"; do
  if [ -f "\$candidate" ]; then
    LEDGRID_HAT=0 bash "\$candidate"
    exit 0
  fi
done
echo "[ERROR] configure_spi.sh not found under ~/\${deploy_dir}" >&2
exit 1
EOF
    ); then
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
    if [ "$status" != "needs_reboot" ]; then
        _deploy_log_error "Unexpected SPI boot configuration status: $status"
        return 1
    fi

    if [ "$config_changed" = "1" ]; then
        _deploy_log_info "Boot config updated. Rebooting $PI_HOST..."
    else
        _deploy_log_info "SPI device nodes missing. Rebooting $PI_HOST..."
    fi
    ssh "${SSH_OPTS[@]}" "$PI_HOST" "sudo -n reboot" >/dev/null 2>&1 || true

    if ! wait_for_remote_ssh; then
        return 1
    fi

    if remote_spi_devices_ready >/dev/null 2>&1; then
        _deploy_log_success "SPI devices ready after reboot"
        remote_spi_devices_ready
        return 0
    fi

    _deploy_log_error "Required SPI devices still missing after reboot"
    _deploy_log_error "Expected: /dev/spidev0.0 /dev/spidev0.1 /dev/spidev0.3 /dev/spidev1.0 /dev/spidev1.1"
    _deploy_log_error "Check boot config on the Pi and run deploy again"
    return 1
}
