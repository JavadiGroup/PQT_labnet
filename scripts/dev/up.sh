#!/usr/bin/env bash
# Start the local development network in dependency order and wait until each
# component reports ready:
#   Central (:8008)  ->  every Lab PC in scripts/dev/config.sh  ->  Control Center (:8080)
#   ->  the safe-state watchdog for Automation grants (health on :8081)
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
    ok "Central already running (restart.sh reloads code changes)"
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
        ok "$lab_pc_id already running (restart.sh reloads code changes)"
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
    ok "Control Center already running (restart.sh reloads code changes)"
else
    [[ -f "$DEVNET_DIR/$CONTROL_CENTER_USER.env" ]] || die "No Control Center settings. Re-run scripts/dev/seed.sh."
    log "Starting the Control Center"
    start_process control-center "$CONTROL_CENTER_PORT" "$log_file" \
        "$(native_path "$(labnet_bin labnet-control-center)")" \
        --env-file "$(native_path "$DEVNET_DIR/$CONTROL_CENTER_USER.env")"
fi
wait_for_http "http://127.0.0.1:$CONTROL_CENTER_PORT/health/ready" 30 "$log_file"
ok "Control Center ready"

# The safe-state watchdog: applies an Automation grant's safe state if the
# Control Center dies or hangs mid-grant. It has no web page, only a health port.
log_file="$DEVNET_DIR/logs/automation-watchdog.log"
watchdog_env="$DEVNET_DIR/$WATCHDOG_USER.env"
if port_in_use "$WATCHDOG_PORT"; then
    ok "Safe-state watchdog already running (restart.sh reloads code changes)"
    wait_for_http "http://127.0.0.1:$WATCHDOG_PORT/health/ready" 30 "$log_file"
elif [[ ! -f "$watchdog_env" ]]; then
    warn "No safe-state watchdog settings (this network was seeded before it existed)."
    warn "Run scripts/dev/seed.sh --watchdog, then scripts/dev/up.sh again."
else
    watchdog_command=("$(native_path "$(labnet_bin labnet-watchdog)")")
    if [[ ! -e "$(labnet_bin labnet-watchdog)" && ! -e "$(labnet_bin labnet-watchdog).exe" ]]; then
        # .venv was set up before the command existed (setup.sh adds it): run the module instead.
        watchdog_command=("$(native_path "$PYTHON")" -m labnet_control_center.watchdog)
    fi
    log "Starting the safe-state watchdog"
    start_process automation-watchdog "$WATCHDOG_PORT" "$log_file" "${watchdog_command[@]}" \
        --env-file "$(native_path "$watchdog_env")"
    wait_for_http "http://127.0.0.1:$WATCHDOG_PORT/health/ready" 30 "$log_file"
    ok "Safe-state watchdog ready"
fi

ok "Network is up. Control Center: http://127.0.0.1:$CONTROL_CENTER_PORT/status"
echo "Inspect: scripts/dev/status.sh    Exercise: scripts/dev/smoke.sh    Stop: scripts/dev/down.sh"
