#!/bin/sh
# Name: Stop Literary Clock Phase 4B.2 Power Study
# Author: Literary Clock
# DontUseFBInk

set -u

runtime_root=/mnt/us/literary-clock/runtime
release=$(sed -n '1p' "$runtime_root/current-release" 2>/dev/null || true)
case "$release" in "" | */* | *..*) echo "current release is missing or invalid" >&2; exit 70 ;; esac
exec "$runtime_root/releases/$release/bin/literary-clock-power-study-stop.sh"
