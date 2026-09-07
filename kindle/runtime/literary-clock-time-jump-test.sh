#!/bin/sh
# Name: Literary Clock Phase 4B Time Jump Test
# Author: Literary Clock
# DontUseFBInk
#
# Bounded test: the user changes/restores device time through Kindle Settings.
# This script only observes one local timestamp and renders it.

set -u

base=/mnt/us/literary-clock
launcher=$base/runtime/literary-clock-launch-current.sh
status=$base/runtime/time-jump-status.txt
awesome_pids=
cvm_pids=
power_enabled=0

exec >> "$status" 2>&1

restore() {
    rc=$?
    trap - 0 1 2 15
    if test -n "$cvm_pids"; then kill -CONT $cvm_pids 2>/dev/null || true; fi
    if test -n "$awesome_pids"; then kill -CONT $awesome_pids 2>/dev/null || true; fi
    if test "$power_enabled" -eq 1; then "$power" off || true; fi
    date '+restored_ui_at_local=%Y-%m-%dT%H:%M:%S%z'
    sync
    exit "$rc"
}
trap restore 0 1 2 15

test -x "$launcher" || { echo "release launcher missing: $launcher"; exit 1; }
release=$(sed -n '1p' "$base/runtime/current-release")
power=$base/runtime/releases/$release/bin/literary-clock-power.sh
test -x "$power" || { echo "active release power helper missing: $power"; exit 1; }

# One snapshot is logged for audit; runtime independently takes exactly one snapshot
# for its own date+minute display transaction.
date '+observed_before_display=%s|%Y-%m-%d|%H%M|%w-%m-%d|%z'
"$power" on
power_enabled=1
awesome_pids=$(pidof awesome 2>/dev/null || true)
cvm_pids=$(pidof cvm 2>/dev/null || true)
if test -n "$awesome_pids"; then kill -STOP $awesome_pids; fi
if test -n "$cvm_pids"; then kill -STOP $cvm_pids; fi
sleep 2
"$launcher"
runtime_rc=$?
echo "runtime_exit=$runtime_rc"
test "$runtime_rc" -eq 0 || exit "$runtime_rc"
sleep 60
