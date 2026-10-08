#!/usr/bin/env bash
# Build a reviewed release on the development PC.
#
#   1. refuse to run on uncommitted changes (artifacts must match one commit)
#   2. confirm generated protobuf files are current and the tests pass
#   3. build the labnet wheel
#   4. stage everything a runtime host needs in PQT_network_releases/<version>/:
#        labnet-<version>-py3-none-any.whl   the installable package
#        site.zip                            site/: env templates + Lab PC inventories
#        client-examples.zip                 examples/: the 01-05 experiment scripts
#        update_host.sh                      installer run on each runtime host
#        RELEASE.TXT                         git revision, version, SHA-256 hashes
#
# Bump `version` in every packages/*/pyproject.toml and commit before running this;
# an existing release directory is never overwritten.
#
# Usage: scripts/release/build.sh [--output DIR] [--wheelhouse]
#   --output DIR   stage into DIR/<version> instead of PQT_network_releases/<version>
#   --wheelhouse   also download every dependency wheel for offline installs

source "$(dirname "${BASH_SOURCE[0]}")/../lib/common.sh"
require_dev_python

output_root="$REPO_ROOT/PQT_network_releases"
wheelhouse=false
while (($#)); do
    case "$1" in
        --output)
            output_root="${2:?--output needs a directory}"
            shift 2
            ;;
        --wheelhouse)
            wheelhouse=true
            shift
            ;;
        *) die "Unknown option $1." ;;
    esac
done

cd "$REPO_ROOT"
[[ -z "$(git status --porcelain)" ]] ||
    die "Uncommitted changes present. Commit them first so the release matches one revision:
$(git status --short)"

version="$(package_version)"
revision="$(git rev-parse HEAD)"
release_dir="$output_root/$version"
wheel_name="labnet-$version-py3-none-any.whl"
cc_wheel_name="labnet_control_center-$version-py3-none-any.whl"
[[ ! -e "$release_dir" ]] ||
    die "$release_dir already exists. Bump version in packages/*/pyproject.toml and commit."

log "Release $version from $revision"

log "Checking generated contracts and running tests"
(cd "$LABNET_DIR" && "$PYTHON" tools/generate_protos.py --check)
(cd "$REPO_ROOT" && "$PYTHON" -m pytest -q)

log "Building $wheel_name and $cc_wheel_name"
# A stale setuptools build/ directory can leak deleted modules into the wheel.
rm -rf "$LABNET_DIR/build" "$CONTROL_CENTER_DIR/build"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT
"$PYTHON" -m pip wheel --quiet --no-deps "$(native_path "$LABNET_DIR")" "$(native_path "$CONTROL_CENTER_DIR")" \
    --wheel-dir "$(native_path "$staging")"
[[ -f "$staging/$wheel_name" ]] || die "pip did not produce $wheel_name."
[[ -f "$staging/$cc_wheel_name" ]] || die "pip did not produce $cc_wheel_name."

mkdir -p "$release_dir"
cp "$staging/$wheel_name" "$staging/$cc_wheel_name" "$release_dir/"

log "Archiving site configuration and examples from HEAD"
# HEAD:<dir> archives the directory's contents without a <dir>/ prefix.
git archive --format=zip --output="$release_dir/site.zip" HEAD:site
git archive --format=zip --output="$release_dir/client-examples.zip" HEAD:examples
git show "HEAD:scripts/deploy/update_host.sh" > "$release_dir/update_host.sh"

if $wheelhouse; then
    log "Downloading dependency wheels for offline installs"
    "$PYTHON" -m pip download --quiet --dest "$(native_path "$release_dir/wheelhouse")" \
        "$(native_path "$release_dir/$wheel_name")[central,lab-pc]" "$(native_path "$release_dir/$cc_wheel_name")"
fi

cat > "$release_dir/RELEASE.TXT" << EOF
PQT Network Release Record

Git revision:     $revision
Package version:  $version
Wheel filename:   $wheel_name
SHA-256:          $(sha256_upper "$release_dir/$wheel_name")

Archive SHA-256:
  $cc_wheel_name  $(sha256_upper "$release_dir/$cc_wheel_name")
  site.zip                  $(sha256_upper "$release_dir/site.zip")
  client-examples.zip       $(sha256_upper "$release_dir/client-examples.zip")
  update_host.sh            $(sha256_upper "$release_dir/update_host.sh")
EOF

ok "Release staged in $release_dir"
cat "$release_dir/RELEASE.TXT"
echo
echo "Next: copy $release_dir to the release share, then on each host run"
echo "  bash <release dir>/update_host.sh central|lab-pc|client"
