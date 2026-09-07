#!/bin/sh
# Name: Literary Clock Phase 4B.2 Power Audit
# Author: Literary Clock
# DontUseFBInk

# Read-only capability/state inventory. The report is written only to user storage.

set -u

base=/mnt/us/literary-clock
report=$base/phase4b2-power-audit.txt
runtime_root=$base/runtime
kron=$runtime_root/tools/kron
if ! test -x "$kron"; then kron=$runtime_root/bin/kron; fi

mkdir -p "$base"
exec > "$report" 2>&1

section() {
    printf '\n--- %s ---\n' "$1"
}

section timestamp
date '+snapshot=%s|%Y-%m-%d|%H%M|%w-%m-%d|%z'
date -u '+utc=%Y-%m-%dT%H:%M:%SZ'
printf 'uptime='; cat /proc/uptime 2>/dev/null || true

section release
for pointer in current-release previous-release; do
    printf '%s=' "$pointer"
    sed -n '1p' "$runtime_root/$pointer" 2>/dev/null || echo missing
done
current=$(sed -n '1p' "$runtime_root/current-release" 2>/dev/null || true)
if test -n "$current"; then
    sed -n '1,80p' "$runtime_root/releases/$current/release.meta" 2>/dev/null || true
fi

section powerd
lipc-probe -v com.lab126.powerd 2>/dev/null || true
for property in state status preventScreenSaver screenSaverTimeout rtcWakeup rtcWakeup2 \
    battLevel isCharging flIntensity; do
    printf '%s=' "$property"
    lipc-get-prop com.lab126.powerd "$property" 2>/dev/null || echo unavailable
done
printf 'sys_power_state='; cat /sys/power/state 2>/dev/null || echo unavailable
printf 'wakeup_count='; cat /sys/power/wakeup_count 2>/dev/null || echo unavailable

section timing_capabilities
printf 'date_epoch_roundtrip='; date -d @0 '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || echo unsupported
for command_name in lipc-wait-event powerd_test rtcwake flock sync fsync; do
    printf '%s=' "$command_name"
    command -v "$command_name" 2>/dev/null || echo missing
done
if command -v lipc-wait-event >/dev/null 2>&1; then
    lipc-wait-event -h 2>&1 | sed -n '1,80p'
fi

section framework
for process_name in awesome cvm blanket powerd; do
    printf '%s=' "$process_name"
    pidof "$process_name" 2>/dev/null || echo none
done
for property in orientation orientationLock chromeState isScreenSaverLayerWindowActive; do
    printf '%s=' "$property"
    lipc-get-prop com.lab126.winmgr "$property" 2>/dev/null || echo unavailable
done

section scheduler
if test -x "$kron"; then
    "$kron" version 2>&1 || true
    "$kron" -dir "$runtime_root/kron" list 2>&1 || true
else
    echo kron=missing
fi
printf 'kron_pids='; pidof kron 2>/dev/null || echo none

section mounts
df -k /mnt/us /var/local 2>/dev/null || true
mount | grep -E ' /mnt/us | /var/local ' || true
for root in "$runtime_root/state" /var/local/literary-clock; do
    if test -e "$root"; then ls -lad "$root"; else echo "$root=absent"; fi
done

section recent_power_log
if command -v logread >/dev/null 2>&1; then
    logread 2>/dev/null | grep -Ei 'powerd|suspend|readyToSuspend|rtcWakeup' | tail -n 120
fi

section complete
date -u '+completed_at_utc=%Y-%m-%dT%H:%M:%SZ'
sync
