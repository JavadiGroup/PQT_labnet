#!/usr/bin/env bash
# The local change loop in one command, after you have edited protos,
# physical drivers, or inventories:
#
#   down.sh  ->  codegen.sh  ->  up.sh  ->  smoke.sh
#
# Central's database and keys are kept. If you renamed a Lab PC or moved a
# device ID to a different Lab PC, run seed.sh before up.sh instead.
#
# Usage: scripts/dev/redeploy.sh [--skip-tests]

DEV_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
set -euo pipefail

"$DEV_SCRIPTS/down.sh"
"$DEV_SCRIPTS/codegen.sh" "$@"
"$DEV_SCRIPTS/up.sh"
"$DEV_SCRIPTS/smoke.sh"
