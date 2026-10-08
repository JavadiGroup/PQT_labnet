#!/usr/bin/env bash
# Install or upgrade one LabNet role on a runtime host from a staged release.
# This file is copied into every release directory by scripts/release/build.sh,
# so runtime hosts need only Git Bash (Windows) or bash, plus Python 3.11.
#
# Usage: bash <release dir>/update_host.sh <central|lab-pc|client|control-center> [release dir]
#
# Steps:
#   1. verify the wheel (and archives) against the hashes in RELEASE.TXT
#   2. refuse to continue while that role's process is still running
#   3. create the role's virtual environment if needed and install the wheel
#   4. role-specific work:
#        central  back up the SQLite database, then apply schema migrations
#        lab-pc   install site/ (inventories), then check lab-pc.env loads
#        client   install the example scripts
#        control-center  check control-center.env loads
#   5. record the installed release next to the virtual environment
#
# Each role keeps its settings in one file that updates never touch:
#   $LABNET_HOME/central.env   $LABNET_HOME/lab-pc.env   $LABNET_HOME/client.env
#
# Starting and stopping the role's process stays with the operator (or the
# service manager); the script prints the start command at the end.
#
# Environment (all optional):
#   LABNET_HOME          install root. Default C:/ProgramData/LabNet on Windows,
#                        /opt/labnet elsewhere
#   BASE_PYTHON          interpreter for new venvs. Default "py -3.11" / python3.11
#   CENTRAL_PORT         Central's loopback port to check. Default 8008
#   LAB_PC_HEALTH_PORT   Lab-PC health port to check. Default 9001

set -euo pipefail

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn\033[0m %s\n' "$*" >&2; }
die() {
    printf '\033[1;31merror\033[0m %s\n' "$*" >&2
    exit 1
}

is_windows() { [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* || "$(uname -s)" == CYGWIN* ]]; }
native_path() { if command -v cygpath > /dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
sha256_upper() {
    local digest
    if command -v sha256sum > /dev/null 2>&1; then digest="$(sha256sum "$1")"; else digest="$(shasum -a 256 "$1")"; fi
    printf '%s' "${digest%% *}" | tr '[:lower:]' '[:upper:]'
}
upper() { printf '%s' "$1" | tr '[:lower:]' '[:upper:]'; }

role="${1:-}"
release_dir="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
case "$role" in
    central) extra="[central]" ;;
    lab-pc) extra="[lab-pc]" ;;
    client) extra="" ;;
    control-center) extra="[control-center]" ;;
    *) die "Usage: update_host.sh <central|lab-pc|client|control-center> [release dir]" ;;
esac

if is_windows; then
    LABNET_HOME="${LABNET_HOME:-C:/ProgramData/LabNet}"
    default_python="py -3.11"
else
    LABNET_HOME="${LABNET_HOME:-/opt/labnet}"
    default_python="python3.11"
fi
BASE_PYTHON="${BASE_PYTHON:-$default_python}"
venv_dir="$LABNET_HOME/$role-venv"
settings_file="$LABNET_HOME/$role.env"

# --- 1. Verify the release ---------------------------------------------------
release_file="$release_dir/RELEASE.TXT"
[[ -f "$release_file" ]] || die "No RELEASE.TXT in $release_dir."
field() { tr -d '\r' < "$release_file" | sed -n "s/^$1:[[:space:]]*//p" | head -n 1; }
version="$(field 'Package version')"
wheel_name="$(field 'Wheel filename')"
expected_hash="$(field 'SHA-256')"
wheel="$release_dir/$wheel_name"
[[ -f "$wheel" ]] || die "Missing $wheel."

log "Verifying LabNet $version from $release_dir"
actual_hash="$(sha256_upper "$wheel")"
[[ "$actual_hash" == "$(upper "$expected_hash")" ]] ||
    die "Wheel hash mismatch. Expected $expected_hash, got $actual_hash. Do not install this file."
ok "$wheel_name matches RELEASE.TXT"

verify_archive() {
    local name="$1" expected
    [[ -f "$release_dir/$name" ]] || die "Missing $release_dir/$name."
    expected="$(tr -d '\r' < "$release_file" | awk -v n="$name" '$1 == n { print $2 }')"
    [[ -n "$expected" ]] || die "RELEASE.TXT has no hash for $name."
    [[ "$(sha256_upper "$release_dir/$name")" == "$(upper "$expected")" ]] ||
        die "$name hash mismatch. Do not install this release."
    ok "$name matches RELEASE.TXT"
}

# --- 2. The role must be stopped --------------------------------------------
running_on() { curl --silent --max-time 2 --output /dev/null "http://127.0.0.1:$1/health/live"; }
case "$role" in
    central) port="${CENTRAL_PORT:-8008}" ;;
    lab-pc) port="${LAB_PC_HEALTH_PORT:-9001}" ;;
    client) port="" ;;
    control-center) port="${CONTROL_CENTER_PORT:-8080}" ;;
esac
if [[ -n "$port" ]] && running_on "$port"; then
    die "$role is still running (answers on 127.0.0.1:$port). Stop it, then re-run this script."
fi
# The safe-state watchdog runs from the Control Center's environment too.
if [[ "$role" == control-center ]] && running_on "${WATCHDOG_PORT:-8081}"; then
    die "The safe-state watchdog is still running (answers on 127.0.0.1:${WATCHDOG_PORT:-8081}). Stop it (Ctrl+C in its window, or Stop-ScheduledTask \"LabNet safe-state watchdog\"), then re-run this script."
fi
if [[ "$role" == central && ! -f "$settings_file" ]]; then
    die "Create $settings_file first (template: site/central.env.example in the release's site.zip)."
fi

# --- 3. Install the wheel ----------------------------------------------------
if [[ -x "$venv_dir/Scripts/python.exe" ]]; then
    python="$venv_dir/Scripts/python.exe"
elif [[ -x "$venv_dir/bin/python" ]]; then
    python="$venv_dir/bin/python"
else
    log "Creating $venv_dir"
    mkdir -p "$LABNET_HOME"
    # shellcheck disable=SC2086  # BASE_PYTHON may be "py -3.11"
    $BASE_PYTHON -m venv "$(native_path "$venv_dir")"
    python="$venv_dir/Scripts/python.exe"
    [[ -x "$python" ]] || python="$venv_dir/bin/python"
fi
bin_dir="$(dirname "$python")"

previous="$("$python" -m pip show labnet 2> /dev/null | tr -d '\r' | sed -n 's/^Version: //p' || true)"
log "Installing labnet $version$extra (was: ${previous:-not installed})"
pip_source=()
if [[ -d "$release_dir/wheelhouse" ]]; then
    pip_source=(--no-index --find-links "$(native_path "$release_dir/wheelhouse")")
fi
"$python" -m pip install --quiet --disable-pip-version-check ${pip_source[@]+"${pip_source[@]}"} "$(native_path "$wheel")$extra"
installed="$("$python" -m pip show labnet | tr -d '\r' | sed -n 's/^Version: //p')"
[[ "$installed" == "$version" ]] || die "Expected labnet $version in $venv_dir, found $installed."
ok "labnet $installed installed in $venv_dir"

# --- 4. Role-specific steps ---------------------------------------------------
# replace_tree ZIP TARGET: unpack into TARGET.new, keep the old tree as
# TARGET.previous for rollback, then swap. Settings files live outside it.
replace_tree() {
    local zip="$1" target="$2"
    rm -rf "$target.new"
    "$python" -m zipfile -e "$(native_path "$zip")" "$(native_path "$target.new")"
    if [[ -d "$target" ]]; then
        rm -rf "$target.previous"
        mv "$target" "$target.previous"
        ok "previous copy kept in $target.previous for rollback"
    fi
    mv "$target.new" "$target"
    ok "installed $target"
}

case "$role" in
    central)
        database_url="$(tr -d '\r' < "$settings_file" | sed -n 's/^LABNET_DATABASE_URL=//p' | tail -n 1)"
        database_url="${LABNET_DATABASE_URL:-$database_url}"
        if [[ "$database_url" == sqlite:///* ]]; then
            database="${database_url#sqlite:///}"
            if [[ -f "$database" ]]; then
                backup="$database.before-$version.$(date +%Y%m%d%H%M%S)"
                cp "$database" "$backup"
                ok "database backed up to $backup"
            fi
        else
            warn "Not SQLite: back up the database with its own tools before continuing."
        fi
        log "Applying Central schema migrations"
        "$python" -m labnet.central.manage --env-file "$(native_path "$settings_file")" db upgrade
        ;;
    lab-pc)
        verify_archive site.zip
        replace_tree "$release_dir/site.zip" "$LABNET_HOME/site"
        if [[ -f "$settings_file" ]]; then
            # Load the settings and import the inventory now, so a mistake
            # shows up here instead of when the service next starts.
            log "Checking $settings_file"
            "$python" - "$(native_path "$settings_file")" << 'EOF'
import sys
from labnet.lab_pc.app import load_device_factory
from labnet.lab_pc.config import LabPCSettings

settings = LabPCSettings.from_env(sys.argv[1])
load_device_factory(settings.device_inventory)
print(f" ok settings load; inventory {settings.device_inventory}")
EOF
        else
            warn "No $settings_file yet. Start from site/lab-pc.env.example and the file from setup --write-env."
        fi
        ;;
    client)
        verify_archive client-examples.zip
        replace_tree "$release_dir/client-examples.zip" "$LABNET_HOME/client-app"
        [[ -f "$settings_file" ]] ||
            warn "No $settings_file yet. Copy the researcher file from setup --write-env there."
        ;;
    control-center)
        # It belongs next to Central and reaches it over loopback.
        [[ -f "$LABNET_HOME/central.env" ]] ||
            warn "No $LABNET_HOME/central.env here. The Control Center is meant to run on central_server_pc."
        if [[ -f "$settings_file" ]]; then
            log "Checking $settings_file"
            "$python" -c "import sys; from labnet.control_center.config import ControlCenterSettings as S; s = S.from_env(sys.argv[1]); print(' ok settings load; Central', s.server_url)" "$(native_path "$settings_file")"
        else
            warn "No $settings_file yet. Start from site/control-center.env.example and the file from setup --write-env."
        fi
        ;;
esac

# --- 5. Record what is installed --------------------------------------------
cp "$release_file" "$LABNET_HOME/$role-RELEASE.TXT"
ok "Recorded release in $LABNET_HOME/$role-RELEASE.TXT"

echo
echo "Next: start $role and check it:"
case "$role" in
    central) echo "  $bin_dir/labnet-central --env-file $settings_file   then   curl http://127.0.0.1:8008/health/ready" ;;
    lab-pc) echo "  $bin_dir/labnet-lab-pc --env-file $settings_file   then   curl http://127.0.0.1:9001/health/ready" ;;
    client) echo "  LABNET_ENV_FILE=$settings_file $python $LABNET_HOME/client-app/01_hello_device.py" ;;
    control-center)
        echo "  $bin_dir/labnet-control-center --env-file $settings_file   then   curl http://127.0.0.1:8080/health/ready"
        echo "  $bin_dir/labnet-automation-watchdog --env-file $LABNET_HOME/watchdog.env   then   curl http://127.0.0.1:8081/health/ready"
        ;;
esac
