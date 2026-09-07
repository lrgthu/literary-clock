#!/bin/sh
# Name: Literary Clock Phase 4B Offline Pilot
# Author: Literary Clock
# DontUseFBInk
#
# Bounded pilot only: no boot hook and no permanent scheduler installation.

set -u

runtime_root=/mnt/us/literary-clock/runtime
launcher=$runtime_root/literary-clock-launch-current.sh
kron=${LITCLOCK_KRON:-$runtime_root/tools/kron}
if ! test -x "$kron"; then kron=$runtime_root/bin/kron; fi
pilot_seconds=${LITCLOCK_PILOT_SECONDS:-420}
update_minutes=${LITCLOCK_UPDATE_MINUTES:-1}
status=$runtime_root/pilot-status.txt
awesome_pids=
cvm_pids=
kron_pid=
power_enabled=0

exec >> "$status" 2>&1

restore() {
    rc=$?
    trap - 0 1 2 15
    if test -n "$kron_pid"; then
        kill "$kron_pid" 2>/dev/null || true
        wait "$kron_pid" 2>/dev/null || true
    fi
    if test -n "$cvm_pids"; then kill -CONT $cvm_pids 2>/dev/null || true; fi
    if test -n "$awesome_pids"; then kill -CONT $awesome_pids 2>/dev/null || true; fi
    if test "$power_enabled" -eq 1; then "$power" off || true; fi
    date -u '+pilot_restored_at_utc=%Y-%m-%dT%H:%M:%SZ'
    lipc-get-prop com.lab126.powerd state 2>/dev/null || true
    sync
    exit "$rc"
}
trap restore 0 1 2 15

test -x "$launcher" || { echo "release launcher missing: $launcher"; exit 1; }
test -x "$kron" || { echo "kron missing: $kron"; exit 1; }
case "$update_minutes" in
    1) schedule='* * * * *' ;;
    2 | 3 | 5) schedule="*/$update_minutes * * * *" ;;
    *) echo "LITCLOCK_UPDATE_MINUTES must be 1, 2, 3, or 5"; exit 1 ;;
esac
release=$(sed -n '1p' "$runtime_root/current-release")
release_root=$runtime_root/releases/$release
power=$release_root/bin/literary-clock-power.sh
test -x "$power" || { echo "active release power helper missing: $power"; exit 1; }

echo "pilot_seconds=$pilot_seconds"
echo "update_minutes=$update_minutes"
date '+pilot_start_local=%Y-%m-%dT%H:%M:%S%z'
lipc-get-prop com.lab126.powerd battLevel 2>/dev/null | sed 's/^/battery_before=/'
lipc-get-prop com.lab126.powerd flIntensity 2>/dev/null | sed 's/^/frontlight_before=/'
"$power" on
power_enabled=1

awesome_pids=$(pidof awesome 2>/dev/null || true)
cvm_pids=$(pidof cvm 2>/dev/null || true)
if test -n "$awesome_pids"; then kill -STOP $awesome_pids; fi
if test -n "$cvm_pids"; then kill -STOP $cvm_pids; fi
sleep 2

# kron v0.2.0 reads/rearms the soonest job when its daemon starts. Register first;
# adding after startup is not observed until another timer/powerd event causes a rearm.
# The binary runs directly from /mnt/us: no setup, rootfs symlink or boot hook.
"$kron" -dir "$runtime_root/kron" remove literary-clock-minute >/dev/null 2>&1 || true
export LITCLOCK_SCHEDULER_SOURCE=kron
"$kron" -dir "$runtime_root/kron" add -timeout 30s literary-clock-minute "$schedule" "$launcher"
"$kron" -dir "$runtime_root/kron" -keepawake off -wakelead 5s -jobtimeout 30s daemon &
kron_pid=$!
sleep 2
echo "kron_pid=$kron_pid"
"$kron" -dir "$runtime_root/kron" list || true

"$launcher"

sleep "$pilot_seconds"

"$kron" -dir "$runtime_root/kron" remove literary-clock-minute || true
"$kron" -dir "$runtime_root/kron" stop || true
lipc-get-prop com.lab126.powerd battLevel 2>/dev/null | sed 's/^/battery_after=/'
lipc-get-prop com.lab126.powerd flIntensity 2>/dev/null | sed 's/^/frontlight_after=/'
lipc-get-prop com.lab126.powerd state 2>/dev/null | sed 's/^/power_state_after=/'
date '+pilot_end_local=%Y-%m-%dT%H:%M:%S%z'
