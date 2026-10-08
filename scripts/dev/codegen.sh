#!/usr/bin/env bash
# Regenerate Python artifacts from packages/labnet/protos and verify them:
#   1. tools/generate_protos.py          compile + validate every .proto,
#                                        rewrite labnet/grpc/generated/
#   2. tools/generate_protos.py --check  confirm the committed files match
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

cd "$LABNET_DIR"
log "Generating protobuf bindings and device contracts"
"$PYTHON" tools/generate_protos.py
"$PYTHON" tools/generate_protos.py --check

if $run_tests; then
    log "Running test suite"
    (cd "$REPO_ROOT" && "$PYTHON" -m pytest -q)
fi

ok "Code generation complete."
git -C "$REPO_ROOT" status --short -- packages/labnet/src/labnet/grpc/generated
