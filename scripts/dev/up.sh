#!/usr/bin/env bash
# Start the local development network in dependency order and wait until each
# component reports ready:
#   Central (:8008)  ->  every Lab PC in scripts/dev/config.sh  ->  Control Center (:8080)
#
# Processes run in the background; logs go to .devnet/logs/. Stop them with
# scripts/dev/down.sh.
#
# Usage: scripts/dev/up.sh

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_ROOT/scripts/dev/config.sh"
clear_labnet_environment
require_dev_python
[[ -f "$DEVNET_DIR/central.env" ]] || die "No seeded network found. Run scripts/dev/seed.sh first."
export PYTHONUNBUFFERED=1

if port_in_use "$CENTRAL_PORT"; then
    ok "Central already running (run down.sh first to load code changes)"
else
    log "Starting Central on $CENTRAL_URL"
    start_process central "$CENTRAL_PORT" "$DEVNET_DIR/logs/central.log" \
        "$(native_path "$(labnet_bin labnet-central)")" \
        --env-file "$(native_path "$DEVNET_DIR/central.env")"
fi
wait_for_http "$CENTRAL_URL/health/ready" 30 "$DEVNET_DIR/logs/central.log"
ok "Central ready"

for entry in "${LAB_PCS[@]}"; do
    read -r lab_pc_id grpc_port http_port _inventory <<< "$entry"
    log_file="$DEVNET_DIR/logs/$lab_pc_id.log"
    if port_in_use "$http_port"; then
        ok "$lab_pc_id already running (run down.sh first to load code changes)"
    else
        [[ -f "$DEVNET_DIR/$lab_pc_id.env" ]] || die "$lab_pc_id has no settings file. Re-run scripts/dev/seed.sh."
        port_in_use "$grpc_port" && die "gRPC port $grpc_port (for $lab_pc_id) is already in use."
        log "Starting $lab_pc_id (gRPC 127.0.0.1:$grpc_port)"
        start_process "$lab_pc_id" "$http_port" "$log_file" \
            "$(native_path "$(labnet_bin labnet-lab-pc)")" \
            --env-file "$(native_path "$DEVNET_DIR/$lab_pc_id.env")"
    fi
    # Ready means: devices connected, registered with Central, gRPC serving.
    wait_for_http "http://127.0.0.1:$http_port/health/ready" 45 "$log_file"
    ok "$lab_pc_id ready"
done

log_file="$DEVNET_DIR/logs/control-center.log"
if port_in_use "$CONTROL_CENTER_PORT"; then
    ok "Control Center already running (run down.sh first to load code changes)"
else
    [[ -f "$DEVNET_DIR/$CONTROL_CENTER_USER.env" ]] || die "No Control Center settings. Re-run scripts/dev/seed.sh."
    log "Starting the Control Center"
    start_process control-center "$CONTROL_CENTER_PORT" "$log_file" \
        "$(native_path "$(labnet_bin labnet-control-center)")" \
        --env-file "$(native_path "$DEVNET_DIR/$CONTROL_CENTER_USER.env")"
fi
wait_for_http "http://127.0.0.1:$CONTROL_CENTER_PORT/health/ready" 30 "$log_file"
ok "Control Center ready"

ok "Network is up. Control Center: http://127.0.0.1:$CONTROL_CENTER_PORT/status"
echo "Inspect: scripts/dev/status.sh    Exercise: scripts/dev/smoke.sh    Stop: scripts/dev/down.sh"
