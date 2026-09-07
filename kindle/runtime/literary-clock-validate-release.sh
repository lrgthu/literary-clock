#!/bin/sh
# One-time startup validation for the active, immutable runtime release.

set -u

release_root=${1:-}

fail() {
    echo "Literary Clock release validation failed: $1" >&2
    exit "${2:-1}"
}

test -n "$release_root" || fail "release path is required" 2
case "$release_root" in
    /mnt/us/literary-clock/runtime/releases/*) ;;
    *)
        if test "${LITCLOCK_ALLOW_TEST_RELEASE_PATH:-0}" != 1; then
            fail "release is outside the runtime release root" 3
        fi
        ;;
esac
test -d "$release_root" || fail "release directory is missing" 4
test -r "$release_root/release.meta" || fail "release metadata is missing" 5
test -r "$release_root/checksums.sha256" || fail "checksum inventory is missing" 6
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is unavailable" 7

meta_value() {
    awk -F '\t' -v key="$1" '$1 == key { print $2; exit }' "$2"
}

test "$(meta_value release_format_version "$release_root/release.meta")" = 1 || \
    fail "release format is incompatible" 8
test "$(meta_value runtime_version "$release_root/release.meta")" = 2 || \
    fail "runtime version is incompatible" 9
test "$(meta_value renderer_preset "$release_root/release.meta")" = pw4-v1 || \
    fail "renderer preset is incompatible" 10

for required in \
    bundle/bundle.meta \
    bundle/minutes.tsv \
    bundle/quotes.tsv \
    bundle/dates.tsv \
    bin/literary-clock-runtime.sh \
    bin/literary-clock-display.sh \
    bin/literary-clock-service-start.sh \
    bin/literary-clock-service-stop.sh; do
    test -s "$release_root/$required" || fail "required file is absent: $required" 11
done

# This is intentionally a startup/deployment cost, never a per-minute cost.
if ! (CDPATH= cd -- "$release_root" && sha256sum -c checksums.sha256 >/dev/null 2>&1); then
    fail "full checksum inventory did not validate" 12
fi

echo "Literary Clock release validated: $(meta_value release_version "$release_root/release.meta")"
