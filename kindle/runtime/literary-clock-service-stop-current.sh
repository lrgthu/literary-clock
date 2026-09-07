#!/bin/sh
# Stop the release recorded by the active service, falling back to current.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
session=${LITCLOCK_SERVICE_ROOT:-/var/local/literary-clock/service}/session.tsv
release=$(awk -F '\t' '$1 == "release" { print $2; exit }' "$session" 2>/dev/null)
if test -z "$release"; then release=$(sed -n '1p' "$runtime_root/current-release" 2>/dev/null); fi
case "$release" in "" | */* | *..*) echo "service release is missing or invalid" >&2; exit 93 ;; esac
script=$runtime_root/releases/$release/bin/literary-clock-service-stop.sh
if test ! -x "$script"; then
    # Last-resort reversible recovery for a legacy/incomplete release.
    for name in cvm awesome; do
        pids=$(pidof "$name" 2>/dev/null || true)
        if test -n "$pids"; then kill -CONT $pids 2>/dev/null || true; fi
    done
    lipc-set-prop com.lab126.powerd preventScreenSaver 0 >/dev/null 2>&1 || true
    echo "service stop helper is unavailable; stock UI recovery attempted" >&2
    exit 94
fi
exec "$script" "$@"
