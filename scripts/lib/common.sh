# Shared helpers for the LabNet shell scripts. Source this file; do not run it.
#
# Works in Git Bash on Windows and in bash on Linux/macOS.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LABNET_DIR="$REPO_ROOT/Labnet"
DEVNET_DIR="$REPO_ROOT/.devnet"

is_windows() {
    case "$(uname -s)" in
        MINGW* | MSYS* | CYGWIN*) return 0 ;;
        *) return 1 ;;
    esac
}

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn\033[0m %s\n' "$*" >&2; }
die() {
    printf '\033[1;31merror\033[0m %s\n' "$*" >&2
    exit 1
}

# Convert a bash path to the form a native Windows program expects
# (C:/Users/...). On Linux/macOS the path is returned unchanged.
native_path() {
    if command -v cygpath > /dev/null 2>&1; then
        cygpath -m "$1"
    else
        printf '%s' "$1"
    fi
}

# Interpreter used to create a new virtual environment.
base_python() {
    if [[ -n "${BASE_PYTHON:-}" ]]; then
        printf '%s' "$BASE_PYTHON"
    elif is_windows && command -v py > /dev/null 2>&1; then
        printf '%s' "py -3.11"
    elif command -v python3.11 > /dev/null 2>&1; then
        printf '%s' "python3.11"
    else
        printf '%s' "python3"
    fi
}

# Python inside a virtual environment directory.
venv_python() {
    if [[ -x "$1/Scripts/python.exe" ]]; then
        printf '%s' "$1/Scripts/python.exe"
    else
        printf '%s' "$1/bin/python"
    fi
}

# The repository development environment (override with PYTHON=...).
PYTHON="${PYTHON:-$(venv_python "$REPO_ROOT/.venv")}"

require_dev_python() {
    [[ -x "$PYTHON" ]] || die "No development environment at $REPO_ROOT/.venv. Run scripts/dev/setup.sh first."
    [[ -e "$(labnet_bin labnet-lab-pc)" || -e "$(labnet_bin labnet-lab-pc).exe" ]] ||
        die "LabNet commands are missing from .venv. Run scripts/dev/setup.sh."
}

# Path of an installed LabNet command (labnet-central, labnet-lab-pc, ...)
# next to the development interpreter.
labnet_bin() {
    printf '%s/%s' "$(dirname "$PYTHON")" "$1"
}

# Remove LABNET_* variables inherited from the calling shell so that a stray
# value (for example LABNET_LAB_PC_ID) cannot override the generated files.
clear_labnet_environment() {
    local name
    for name in $(compgen -e | grep '^LABNET_' || true); do
        unset "$name"
    done
}

# Return success when an HTTP endpoint answers 2xx.
http_ok() {
    curl --silent --fail --max-time 2 --output /dev/null "$1"
}

# wait_for_http URL TIMEOUT_SECONDS LOG_FILE
wait_for_http() {
    local url="$1" timeout="$2" log_file="${3:-}" waited=0
    until http_ok "$url"; do
        if ((waited >= timeout)); then
            if [[ -n "$log_file" && -f "$log_file" ]]; then
                warn "Last lines of $log_file:"
                tail -n 25 "$log_file" >&2
            fi
            die "$url was not ready after ${timeout}s."
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

# Background processes are identified by the TCP port they listen on rather
# than by a PID file. On Windows the venv python.exe is a launcher that starts
# a second python.exe, so the PID bash sees is not the server; the listening
# port always identifies the real server, even one started by hand.

# listener_pid PORT -> PID listening on that local TCP port, or nothing.
listener_pid() {
    local port="$1"
    if is_windows; then
        # Listening sockets have a foreign address of 0.0.0.0:0 or [::]:0
        # (matching on that instead of "LISTENING" works on any Windows locale).
        netstat -ano -p TCP | tr -d '\r' |
            awk -v suffix=":$port" '$3 ~ /:0$/ && substr($2, length($2) - length(suffix) + 1) == suffix { print $5; exit }'
    elif command -v lsof > /dev/null 2>&1; then
        lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2> /dev/null | head -n 1
    else
        ss -ltnpH "sport = :$port" 2> /dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n 1
    fi
}

port_in_use() {
    [[ -n "$(listener_pid "$1")" ]]
}

process_image() {
    if is_windows; then
        tasklist //FI "PID eq $1" //FO CSV //NH 2> /dev/null | tr -d '\r"' | cut -d, -f1
    else
        ps -p "$1" -o comm= 2> /dev/null
    fi
}

# start_process NAME PORT LOG_FILE COMMAND...
# Runs COMMAND detached in the background from the repository root, logging to
# LOG_FILE. Python does the spawning because a plain `cmd &` from Git Bash lets
# the Windows server inherit this shell's pipes, so `up.sh | tee` would never end.
start_process() {
    local name="$1" port="$2" log_file="$3"
    shift 3
    mkdir -p "$(dirname "$log_file")"
    if port_in_use "$port"; then
        die "Port $port (for $name) is already in use. Run scripts/dev/down.sh first."
    fi
    (cd "$REPO_ROOT" && "$PYTHON" - "$(native_path "$log_file")" "$@" << 'EOF'
import os, subprocess, sys
windows = os.name == "nt"
with open(sys.argv[1], "wb") as log:
    subprocess.Popen(
        sys.argv[2:],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        close_fds=True,
        # CREATE_NO_WINDOW: one hidden console shared with the venv launcher's
        # child, so no console windows pop up (closing one would kill a server).
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if windows else 0,
        start_new_session=not windows,
    )
EOF
    )
}

# stop_process NAME PORT: stop the Python process listening on PORT.
stop_process() {
    local name="$1" port="$2" pid image waited=0
    pid="$(listener_pid "$port")"
    [[ -n "$pid" ]] || return 0

    image="$(process_image "$pid")"
    if [[ "$image" != *python* ]]; then
        warn "Port $port is held by '$image' (pid $pid), not Python; leaving it alone."
        return 0
    fi

    if is_windows; then
        taskkill //PID "$pid" //T //F > /dev/null 2>&1 || true
    else
        # SIGTERM lets Uvicorn run its shutdown hooks (devices disconnect).
        kill "$pid" 2> /dev/null || true
    fi
    while port_in_use "$port" && ((waited < 15)); do
        sleep 1
        waited=$((waited + 1))
    done
    if port_in_use "$port"; then
        is_windows || kill -9 "$pid" 2> /dev/null || true
        sleep 1
    fi
    port_in_use "$port" && die "Could not stop $name on port $port (pid $pid)."
    ok "stopped $name (port $port)"
}

# Read version = "x.y.z" from Labnet/pyproject.toml.
package_version() {
    "$PYTHON" - "$LABNET_DIR/pyproject.toml" << 'EOF'
import sys, tomllib
with open(sys.argv[1], "rb") as handle:
    print(tomllib.load(handle)["project"]["version"])
EOF
}

# SHA-256 of a file, upper-case hex (matches PowerShell Get-FileHash output).
sha256_upper() {
    local digest
    if command -v sha256sum > /dev/null 2>&1; then
        digest="$(sha256sum "$1")"
    else
        digest="$(shasum -a 256 "$1")"
    fi
    printf '%s' "${digest%% *}" | tr '[:lower:]' '[:upper:]'
}
