#!/bin/sh
# Stable pointer resolver: execute runtime code from the active atomic release.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
pointer=${LITCLOCK_CURRENT_RELEASE_POINTER:-$runtime_root/current-release}

test -r "$pointer" || {
    echo "Literary Clock current release pointer is missing" >&2
    exit 40
}
release=$(sed -n '1p' "$pointer")
case "$release" in
    "" | */* | *..*)
        echo "Literary Clock current release pointer is invalid" >&2
        exit 41
        ;;
esac
runtime=$runtime_root/releases/$release/bin/literary-clock-runtime.sh
test -x "$runtime" || {
    echo "Literary Clock active runtime is missing" >&2
    exit 42
}
export LITCLOCK_RELEASE_ROOT=$runtime_root/releases/$release
exec "$runtime" "$@"
