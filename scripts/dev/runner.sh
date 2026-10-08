#!/usr/bin/env bash
# The Automation runner for the local development network: Docker in WSL on
# this laptop, talking to the dev Control Center at http://127.0.0.1:8080.
#
#   scripts/dev/runner.sh build        # build the three images (after code changes too)
#   scripts/dev/runner.sh login        # once: sign Claude in, inside the container (/login)
#   scripts/dev/runner.sh up           # start the egress proxy and the runner
#   scripts/dev/runner.sh self-check   # prove the isolation holds (the runner checks at start too)
#   scripts/dev/runner.sh logs         # follow the runner's log
#   scripts/dev/runner.sh down         # stop them
#
# Needs: the dev network up (scripts/dev/up.sh); WSL 2 with mirrored networking
# (networkingMode=mirrored in %UserProfile%\.wslconfig) so WSL reaches the
# laptop's 127.0.0.1; Docker Engine in that WSL distro, usable without sudo.
# The first `up` issues a runner token (labnet-control-center-admin runner add
# dev-laptop) into .devnet/runner.env.
#
# The lab doesn't use this script: see "Automation runner" in Labnet/README.md.
#
# Usage: scripts/dev/runner.sh build|login|up|down|logs|self-check|status

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_ROOT/scripts/dev/config.sh"

WSL_DISTRO="${LABNET_WSL_DISTRO:-Ubuntu-22.04}"
RUNNER_NAME="${LABNET_DEV_RUNNER_NAME:-dev-laptop}"
RUNNER_ENV="$DEVNET_DIR/runner.env"
COMPOSE_FILE="docker/automation/compose.yaml"

is_windows || die "runner.sh is for the Windows dev laptop (Docker in WSL). On Linux, use docker compose directly."
command -v wsl.exe > /dev/null || die "WSL isn't installed. Install it (wsl --install -d $WSL_DISTRO)."

# A Windows path as WSL sees it: C:/Users/... -> /mnt/c/Users/...
wsl_path() {
    local windows
    windows="$(native_path "$1")"
    windows="${windows//\\//}"
    printf '/mnt/%s%s' "$(printf '%s' "${windows:0:1}" | tr '[:upper:]' '[:lower:]')" "${windows:2}"
}

in_wsl() {
    # MSYS would rewrite the /mnt/... arguments into Windows paths; keep them as they are.
    MSYS_NO_PATHCONV=1 wsl.exe -d "$WSL_DISTRO" -e bash -lc "$1"
}

REPO_IN_WSL="$(wsl_path "$REPO_ROOT")"
ENV_IN_WSL="$(wsl_path "$RUNNER_ENV")"

compose() {
    # The work folder lives in WSL's own file system (fast, and Linux permissions work there).
    in_wsl "set -e
        export LABNET_RUNNER_WORK_DIR=\"\$HOME/.labnet-runner\" LABNET_RUNNER_ENV_FILE='$ENV_IN_WSL'
        export LABNET_RUNNER_CONFIG_DIR=\"\$HOME/.labnet-runner-config\"  # no CA file in development
        mkdir -p \"\$LABNET_RUNNER_WORK_DIR\" \"\$LABNET_RUNNER_CONFIG_DIR\"
        cd '$REPO_IN_WSL' && docker compose -f '$COMPOSE_FILE' $*"
}

check_wsl() {
    in_wsl 'docker info > /dev/null 2>&1' \
        || die "Docker in WSL ($WSL_DISTRO) isn't usable without sudo. In WSL: sudo usermod -aG docker \$USER, then wsl --shutdown."
    [[ "$(wsl.exe -d "$WSL_DISTRO" -- wslinfo --networking-mode 2> /dev/null | tr -d '\r')" == mirrored ]] \
        || die "WSL isn't in mirrored networking mode, so it can't reach the Control Center at 127.0.0.1. Put networkingMode=mirrored under [wsl2] in %UserProfile%\\.wslconfig, then wsl --shutdown."
}

write_env() {
    [[ -f "$RUNNER_ENV" ]] && return
    http_ok "http://127.0.0.1:$CONTROL_CENTER_PORT/health/live" \
        || die "The dev Control Center isn't running; start the network first (scripts/dev/up.sh)."
    local admin token
    admin="$(labnet_bin labnet-control-center-admin)"
    # A token is shown only once: a lost runner.env means a new runner entry.
    "$admin" --env-file "$DEVNET_DIR/control-center.env" runner revoke "$RUNNER_NAME" > /dev/null 2>&1 || true
    token="$("$admin" --env-file "$DEVNET_DIR/control-center.env" runner add "$RUNNER_NAME" | grep -o 'lnr_[A-Za-z0-9_-]*')"
    [[ -n "$token" ]] || die "labnet-control-center-admin didn't print a runner token."
    umask 077
    cat > "$RUNNER_ENV" <<EOF
# The dev runner (scripts/dev/runner.sh). Development only: plain HTTP to the
# laptop's own Control Center; the lab uses its HTTPS address and a CA file.
LABNET_RUNNER_CONTROL_CENTER_URL=http://127.0.0.1:$CONTROL_CENTER_PORT
LABNET_RUNNER_TOKEN=$token
LABNET_RUNNER_DEV=1
EOF
    # The work folder path is WSL's, so the runner reads it from compose's environment instead.
    ok "Runner '$RUNNER_NAME' issued; its token is in $RUNNER_ENV"
}

# WSL stops a distribution (and its Docker containers) soon after the last
# Windows program attached to it exits. While the runner is up, a hidden
# `wsl.exe ... sleep` keeps it running, as Docker Desktop does.
KEEPALIVE_TAG="labnet-runner-keepalive"

keepalive_running() {
    powershell.exe -NoProfile -Command "@(Get-CimInstance Win32_Process -Filter \"Name='wsl.exe'\" | Where-Object { \$_.CommandLine -like '*$KEEPALIVE_TAG*' }).Count" 2> /dev/null | tr -d '\r' | grep -qv '^0$'
}

start_keepalive() {
    keepalive_running && return
    # One argument string: Start-Process joins a list with spaces and no quoting.
    powershell.exe -NoProfile -Command "Start-Process wsl.exe -WindowStyle Hidden -ArgumentList '-d $WSL_DISTRO -e bash -c \"exec -a $KEEPALIVE_TAG sleep infinity\"'" \
        || die "Couldn't start the hidden process that keeps WSL running."
}

stop_keepalive() {
    powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='wsl.exe'\" | Where-Object { \$_.CommandLine -like '*$KEEPALIVE_TAG*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" > /dev/null 2>&1 || true
}

case "${1:-}" in
    build)
        check_wsl
        log "Building the runner, agent and run images (in WSL)"
        compose build
        ok "Images built" ;;
    login)
        check_wsl
        log "Sign Claude in: type /login, follow the link, then /exit"
        MSYS_NO_PATHCONV=1 wsl.exe -d "$WSL_DISTRO" -e bash -lc \
            "cd '$REPO_IN_WSL' && docker compose -f '$COMPOSE_FILE' --profile login run --rm login" ;;
    up)
        check_wsl
        write_env
        log "Starting the egress proxy and the runner"
        start_keepalive
        compose up -d egress runner
        ok "Runner started (WSL is kept running until runner.sh down). It checks its isolation first: scripts/dev/runner.sh logs" ;;
    down)
        check_wsl
        compose down
        stop_keepalive
        ok "Runner stopped; WSL may stop again when idle" ;;
    logs)
        compose logs -f --tail 100 runner ;;
    self-check)
        check_wsl
        write_env
        compose up -d egress  # the checks go through the proxy
        compose run --rm --no-deps runner labnet-runner self-check ;;
    status)
        compose ps ;;
    *)
        die "Usage: scripts/dev/runner.sh build|login|up|down|logs|self-check|status" ;;
esac
