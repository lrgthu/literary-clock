#!/bin/sh
# Name: Literary Clock Fullscreen 15 Minute Test
# Author: Literary Clock
# DontUseFBInk

# Draw exactly one pre-rendered frame, hold the stock UI inactive long enough
# to cross the normal 600-second inactivity timeout, and then restore it.

set -u

base=/mnt/us/literary-clock
frame="$base/frame-device-ccw.png"
fbink=/mnt/us/libkh/bin/fbink
power_helper="$base/literary-clock-power.sh"
status="$base/phase4a2-physical-test-status.txt"
sentinel="$base/phase4a2-physical-test-started"
hold_seconds=900
awesome_pids=
cvm_pids=
power_enabled=0

exec >> "$status" 2>&1

restore() {
    rc=$?
    trap - 0 1 2 15
    if [ -n "$cvm_pids" ]; then
        kill -CONT $cvm_pids 2>/dev/null || true
    fi
    if [ -n "$awesome_pids" ]; then
        kill -CONT $awesome_pids 2>/dev/null || true
    fi
    if [ "$power_enabled" -eq 1 ]; then
        "$power_helper" off || true
    fi
    printf 'frontlight_after_restore='
    lipc-get-prop com.lab126.powerd flIntensity 2>/dev/null || true
    date -u '+restored_at_utc=%Y-%m-%dT%H:%M:%SZ'
    sync
    exit "$rc"
}

trap restore 0 1 2 15

if [ -e "$sentinel" ]; then
    echo 'test skipped: this finite physical test was already started'
    exit 0
fi

test -x "$fbink" || {
    echo "FBInk is not executable: $fbink"
    exit 1
}
test -r "$frame" || {
    echo "frame is not readable: $frame"
    exit 1
}
test -x "$power_helper" || {
    echo "power helper is not executable: $power_helper"
    exit 1
}

echo 'native_layout=1448x1072 landscape'
echo 'framebuffer_transport=1072x1448 lossless CCW rotation, no scaling'
echo 'physical_orientation=USB port on left'
echo "hold_seconds=$hold_seconds"
printf 'frontlight_before='
lipc-get-prop com.lab126.powerd flIntensity
printf 'screensaver_timeout='
lipc-get-prop com.lab126.powerd screenSaverTimeout
printf 'prevent_screensaver_before='
lipc-get-prop com.lab126.powerd preventScreenSaver

"$power_helper" on
power_enabled=1

awesome_pids=$(pidof awesome 2>/dev/null || true)
cvm_pids=$(pidof cvm 2>/dev/null || true)
echo "awesome_pids_present=$([ -n "$awesome_pids" ] && echo yes || echo no)"
echo "cvm_pids_present=$([ -n "$cvm_pids" ] && echo yes || echo no)"

if [ -n "$awesome_pids" ]; then
    kill -STOP $awesome_pids
fi
if [ -n "$cvm_pids" ]; then
    kill -STOP $cvm_pids
fi
sleep 2

echo 'command=/mnt/us/libkh/bin/fbink -c -f -w -W GC16 -D PASSTHROUGH -i /mnt/us/literary-clock/frame-device-ccw.png'
"$fbink" -c -f -w -W GC16 -D PASSTHROUGH -i "$frame"
fbink_rc=$?
echo "fbink_exit=$fbink_rc"
[ "$fbink_rc" -eq 0 ] || exit "$fbink_rc"

: > "$sentinel"
date -u '+displayed_at_utc=%Y-%m-%dT%H:%M:%SZ'
sync

sleep "$hold_seconds"

printf 'power_state_after_hold='
lipc-get-prop com.lab126.powerd state 2>/dev/null || true
printf 'prevent_screensaver_after_hold='
lipc-get-prop com.lab126.powerd preventScreenSaver 2>/dev/null || true
printf 'frontlight_after_hold='
lipc-get-prop com.lab126.powerd flIntensity 2>/dev/null || true
date -u '+hold_completed_at_utc=%Y-%m-%dT%H:%M:%SZ'
