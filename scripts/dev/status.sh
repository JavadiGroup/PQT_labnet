#!/usr/bin/env bash
# Show the health of every local component and the device table Central
# hands to clients.
#
# Usage: scripts/dev/status.sh

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_ROOT/scripts/dev/config.sh"
require_dev_python

state() {
    local name="$1" port="$2"
    if http_ok "http://127.0.0.1:$port/health/ready"; then
        ok "$name ready (port $port)"
    elif port_in_use "$port"; then
        warn "$name running but NOT ready - see .devnet/logs/$name.log"
    else
        warn "$name stopped"
    fi
}

log "Components"
state central "$CENTRAL_PORT"
state control-center "$CONTROL_CENTER_PORT"
for entry in "${LAB_PCS[@]}"; do
    read -r lab_pc_id _grpc_port http_port _inventory <<< "$entry"
    state "$lab_pc_id" "$http_port"
done

researcher_env="$DEVNET_DIR/$RESEARCHER_USER.env"
[[ -f "$researcher_env" ]] || die "No $researcher_env. Run scripts/dev/seed.sh."
http_ok "$CENTRAL_URL/health/ready" || exit 1

log "Devices registered with Central"
LABNET_ENV_FILE="$(native_path "$researcher_env")" "$PYTHON" "$REPO_ROOT/scripts/dev/netcheck.py" status
