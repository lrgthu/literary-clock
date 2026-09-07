#!/bin/sh
# Idempotently stop a bounded power study and restore stock UI/power behavior.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
study_root=$runtime_root/power-study
session=$study_root/session.tsv
status=$study_root/status.txt
kron=${LITCLOCK_KRON:-$runtime_root/tools/kron}
if ! test -x "$kron"; then kron=$runtime_root/bin/kron; fi

read_session() {
    awk -F '\t' -v key="$1" '$1 == key { print $2; exit }' "$session" 2>/dev/null
}

process_is() {
    pid=$1
    expected=$2
    test -r "/proc/$pid/cmdline" || return 1
    tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "$expected"
}

signal_if_matching() {
    signal=$1
    expected=$2
    shift 2
    for pid in "$@"; do
        case "$pid" in "" | *[!0-9]*) continue ;; esac
        if test "$pid" != "$$" && test "$pid" != "${PPID:-}" && process_is "$pid" "$expected"; then
            kill "-$signal" "$pid" 2>/dev/null || true
        fi
    done
}

if test -x "$kron"; then
    "$kron" -dir "$runtime_root/kron" remove literary-clock-study >/dev/null 2>&1 || true
    "$kron" -dir "$runtime_root/kron" remove literary-clock-study-stop >/dev/null 2>&1 || true
fi

if test -r "$session"; then
    original_prevent=$(read_session original_prevent)
    awesome_pids=$(read_session awesome_pids)
    cvm_pids=$(read_session cvm_pids)
    event_pid=$(read_session event_pid)
    watchdog_pid=$(read_session watchdog_pid)
    start_epoch=$(read_session start_epoch)
    battery_before=$(read_session battery_before)
    frontlight_before=$(read_session frontlight_before)
    wireless_before=$(read_session wireless_before)
    signal_if_matching TERM lipc-wait-event $event_pid
    signal_if_matching TERM literary-clock-power-study-stop $watchdog_pid
    # PIDs may be reused after a reboot. Never signal a stored PID unless its
    # live command line still belongs to the process recorded by this session.
    signal_if_matching CONT cvm $cvm_pids
    signal_if_matching CONT awesome $awesome_pids
    case "$original_prevent" in 0 | 1) lipc-set-prop com.lab126.powerd preventScreenSaver "$original_prevent" >/dev/null 2>&1 || true ;; esac
    end_epoch=$(date +%s)
    battery_after=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null || echo unknown)
    frontlight_after=$(lipc-get-prop com.lab126.powerd flIntensity 2>/dev/null || echo unknown)
    wireless_after=$(lipc-get-prop com.lab126.cmd wirelessEnable 2>/dev/null || echo unknown)
    {
        echo "study_end_epoch=$end_epoch"
        echo "elapsed_seconds=$((end_epoch - start_epoch))"
        echo "battery_before=$battery_before"
        echo "battery_after=$battery_after"
        echo "frontlight_before=$frontlight_before"
        echo "frontlight_after=$frontlight_after"
        echo "wireless_before=$wireless_before"
        echo "wireless_after=$wireless_after"
        echo "prevent_after=$(lipc-get-prop com.lab126.powerd preventScreenSaver 2>/dev/null || echo unknown)"
        echo "power_state_after=$(lipc-get-prop com.lab126.powerd state 2>/dev/null || echo unknown)"
        echo "study_restored=yes"
    } >> "$status"
    rm -f "$session"
fi
if test -x "$kron"; then "$kron" -dir "$runtime_root/kron" stop >/dev/null 2>&1 || true; fi
sync
exit 0
