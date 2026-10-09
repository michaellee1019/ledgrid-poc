# Dedicated, non-interactive SSH for wall operations. Never use other identities.
PI_HOST="${PI_HOST:-ledgridwall@ledgridwall.local}"
DEPLOY_DIR="${DEPLOY_DIR:-ledgrid-pod}"
SSH_KEY="${SSH_KEY:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.gpt-key}"
if [ ! -f "$SSH_KEY" ] || [ ! -r "$SSH_KEY" ]; then
    echo "Dedicated SSH key is missing or unreadable: $SSH_KEY" >&2
    exit 1
fi
if [[ ! "$DEPLOY_DIR" =~ ^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)*$ ]] \
    || [[ "/$DEPLOY_DIR/" == *"/../"* ]] || [[ "/$DEPLOY_DIR/" == *"/./"* ]]; then
    echo "DEPLOY_DIR must be a named path below the target home" >&2
    exit 1
fi
SSH_ARGS=(-i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10)
printf -v SSH_RSYNC '%q ' ssh "${SSH_ARGS[@]}"
