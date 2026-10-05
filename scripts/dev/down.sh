#!/usr/bin/env bash
# Stop the local development network in reverse dependency order:
#   every Lab PC  ->  Central
#
# Processes are found by the ports in scripts/dev/config.sh, so this also stops
# copies started by hand. Only Python processes are stopped.
#
# The database, keys, and settings in .devnet/ are kept, so scripts/dev/up.sh
# brings the same network back. Use scripts/dev/seed.sh for a clean slate.
#
# Usage: scripts/dev/down.sh

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_ROOT/scripts/dev/config.sh"

log "Stopping local network"
stop_process control-center "$CONTROL_CENTER_PORT"
for entry in "${LAB_PCS[@]}"; do
    read -r lab_pc_id grpc_port http_port _inventory <<< "$entry"
    stop_process "$lab_pc_id" "$http_port"
    stop_process "$lab_pc_id gRPC" "$grpc_port"
done
stop_process central "$CENTRAL_PORT"
ok "Network is down."
