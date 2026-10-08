#!/usr/bin/env bash
# Regenerate Python artifacts from packages/labnet/protos and verify them:
#   1. labnet driver build --core          compile + validate every .proto,
#                                        rewrite labnet/grpc/generated/
#   2. labnet driver build --core --check  confirm the committed files match
#   3. pytest                            contract, device, and full-stack tests
#
# Running processes are not affected; restart them (scripts/dev/up.sh) to load
# the new code.
#
# Usage: scripts/dev/codegen.sh [--skip-tests]

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
require_dev_python

run_tests=true
case "${1:-}" in
    --skip-tests) run_tests=false ;;
    "") ;;
    *) die "Unknown option $1 (expected --skip-tests)." ;;
esac

log "Generating protobuf bindings and device contracts"
"$PYTHON" -m labnet.cli driver build --core
"$PYTHON" -m labnet.cli driver build --core --check --import-check

if $run_tests; then
    log "Running test suite"
    (cd "$REPO_ROOT" && "$PYTHON" -m pytest -q)
fi

ok "Code generation complete."
git -C "$REPO_ROOT" status --short -- packages/labnet/src/labnet/grpc/generated
