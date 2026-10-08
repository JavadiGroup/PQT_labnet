#!/usr/bin/env bash
# Create .venv and install LabNet in editable mode with every role's
# dependencies. Safe to re-run; it upgrades in place.
#
# Usage: scripts/dev/setup.sh

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"

if [[ ! -x "$PYTHON" ]]; then
    log "Creating virtual environment at $REPO_ROOT/.venv"
    # shellcheck disable=SC2046  # base_python may be "py -3.11"
    $(base_python) -m venv "$(native_path "$REPO_ROOT/.venv")"
    PYTHON="$(venv_python "$REPO_ROOT/.venv")"
fi

log "Installing the LabNet packages (editable) with every role's dependencies and the dev tools"
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --quiet \
    -e "$(native_path "$LABNET_DIR")[central,lab-pc,certs]" \
    -e "$(native_path "$CONTROL_CENTER_DIR")" \
    -e "$(native_path "$AUTOMATION_DIR")" \
    --group "$(native_path "$REPO_ROOT/pyproject.toml"):dev"

ok "Environment ready: $PYTHON"
echo "Next: scripts/dev/seed.sh"
