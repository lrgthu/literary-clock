#!/bin/sh
# Resolve and start the service from the atomically active code+asset release.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
pointer=$runtime_root/current-release
test -r "$pointer" || { echo "current release pointer is missing" >&2; exit 90; }
release=$(sed -n '1p' "$pointer")
case "$release" in "" | */* | *..*) echo "current release pointer is invalid" >&2; exit 91 ;; esac
script=$runtime_root/releases/$release/bin/literary-clock-service-start.sh
test -x "$script" || { echo "active release has no service start helper" >&2; exit 92; }
exec "$script" "$@"
