#!/usr/bin/env bash
# End-to-end check as a researcher: for every available device, authenticate
# with Central, take a lease, connect directly to its Lab PC over gRPC, call
# one read RPC, and release the lease. Exits non-zero if any device fails.
#
# Usage: scripts/dev/smoke.sh

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
source "$REPO_ROOT/scripts/dev/config.sh"
require_dev_python
researcher_env="$DEVNET_DIR/$RESEARCHER_USER.env"
[[ -f "$researcher_env" ]] || die "No $researcher_env. Run scripts/dev/seed.sh."

log "Leasing and reading every available device"
LABNET_ENV_FILE="$(native_path "$researcher_env")" "$PYTHON" "$REPO_ROOT/scripts/dev/netcheck.py" smoke
ok "Smoke test passed."
