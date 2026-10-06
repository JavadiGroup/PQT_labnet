#!/usr/bin/env bash
# Restart one or more components of the local development network, leaving
# the rest running, so they load your code or settings changes:
#
#   scripts/dev/restart.sh control-center           # after editing its Python
#   scripts/dev/restart.sh lab-pc-dev-02            # after editing an inventory
#   scripts/dev/restart.sh central control-center
#
# Components: central, control-center, and the Lab PC ids in config.sh.
# Page files (HTML/CSS/JS) need no restart; just refresh the browser.
#
# Usage: scripts/dev/restart.sh COMPONENT...

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_ROOT/scripts/dev/config.sh"

components=(central control-center "${LAB_PCS[@]%% *}")
if (($# == 0)); then
    die "Usage: scripts/dev/restart.sh COMPONENT...   (one or more of: ${components[*]})"
fi

for name in "$@"; do
    case "$name" in
        central) stop_process central "$CENTRAL_PORT" ;;
        control-center) stop_process control-center "$CONTROL_CENTER_PORT" ;;
        *)
            found=false
            for entry in "${LAB_PCS[@]}"; do
                read -r lab_pc_id grpc_port http_port _inventory <<< "$entry"
                if [[ "$name" == "$lab_pc_id" ]]; then
                    stop_process "$lab_pc_id" "$http_port"
                    stop_process "$lab_pc_id gRPC" "$grpc_port"
                    found=true
                fi
            done
            $found || die "Unknown component '$name'. Choose from: ${components[*]}"
            ;;
    esac
done

# up.sh skips whatever is still running and starts only what was stopped.
exec "$REPO_ROOT/scripts/dev/up.sh"
