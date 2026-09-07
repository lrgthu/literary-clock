#!/bin/sh
# Start one automatically bounded power/suspend experiment.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
config=${LITCLOCK_STUDY_CONFIG:-$runtime_root/power-study.conf}
study_root=$runtime_root/power-study
session=$study_root/session.tsv
status=$study_root/status.txt
events=$study_root/power-events.txt
kron=${LITCLOCK_KRON:-$runtime_root/tools/kron}
if ! test -x "$kron"; then kron=$runtime_root/bin/kron; fi
setup_complete=0

abort_setup() {
    rc=$?
    trap - 0 1 2 15
    if test "$setup_complete" -eq 0 && test -x "${stop_script:-}"; then
        "$stop_script" >/dev/null 2>&1 || true
    fi
    exit "$rc"
}

trap abort_setup 0 1 2 15

read_config() {
    value=$(awk -F '=' -v key="$1" '$1 == key { print $2; exit }' "$config" 2>/dev/null)
    if test -n "$value"; then echo "$value"; else echo "$2"; fi
}

mkdir -p "$study_root"
test ! -e "$session" || { echo "a Literary Clock power study is already active" >&2; exit 50; }
test -x "$kron" || { echo "KindleCron is unavailable" >&2; exit 51; }
test -r "$runtime_root/current-release" || { echo "current release pointer is missing" >&2; exit 52; }
release=$(sed -n '1p' "$runtime_root/current-release")
case "$release" in "" | */* | *..*) echo "current release pointer is invalid" >&2; exit 53 ;; esac
release_root=$runtime_root/releases/$release
launcher=$runtime_root/literary-clock-launch-current.sh
stop_script=$release_root/bin/literary-clock-power-study-stop.sh
test -x "$launcher" && test -x "$stop_script" || { echo "release study helpers are missing" >&2; exit 54; }

strategy=$(read_config strategy pause-ui)
cadence=$(read_config cadence_minutes 5)
cycles=$(read_config cycles 4)
state_location=$(read_config state_location us)
refresh_interval=$(read_config full_refresh_interval 15)
keepawake=$(read_config keepawake off)
reset_test_state=$(read_config reset_test_state 0)
case "$strategy" in framework | framework-prevent | pause-ui | pause-ui-prevent) ;; *) echo "invalid strategy" >&2; exit 55 ;; esac
case "$cadence" in 1 | 2 | 3 | 5) ;; *) echo "invalid cadence_minutes" >&2; exit 56 ;; esac
case "$cycles" in "" | *[!0-9]*) echo "invalid cycles" >&2; exit 57 ;; esac
test "$cycles" -ge 2 && test "$cycles" -le 1440 || { echo "cycles must be 2..1440" >&2; exit 57; }
case "$refresh_interval" in "" | *[!0-9]*) echo "invalid full_refresh_interval" >&2; exit 58 ;; esac
test "$refresh_interval" -ge 1 || { echo "invalid full_refresh_interval" >&2; exit 58; }
case "$state_location" in
    us) state_root=$runtime_root/state ;;
    varlocal) state_root=/var/local/literary-clock/state ;;
    *) echo "invalid state_location" >&2; exit 59 ;;
esac
case "$keepawake" in
    off) ;;
    *[smh])
        keepawake_value=${keepawake%?}
        case "$keepawake_value" in "" | *[!0-9]*) echo "invalid keepawake duration" >&2; exit 66 ;; esac
        test "$keepawake_value" -ge 1 || { echo "invalid keepawake duration" >&2; exit 66; }
        ;;
    *) echo "keepawake must be 'off' or a duration such as '20m'" >&2; exit 66 ;;
esac
case "$reset_test_state" in 0 | 1) ;; *) echo "invalid reset_test_state" >&2; exit 68 ;; esac
case "$cadence" in
    1) schedule='* * * * *' ;;
    *) schedule="*/$cadence * * * *" ;;
esac

if test "$reset_test_state" -eq 1; then
    for reset_root in "$runtime_root/state" /var/local/literary-clock/state; do
        if test -d "$reset_root"; then
            rm -f "$reset_root/state.tsv" "$reset_root"/state.*.tmp "$reset_root"/shuffle.*.tsv
            rm -f "$reset_root/run.lock/pid"
            rmdir "$reset_root/run.lock" 2>/dev/null || true
        fi
    done
    : > "$runtime_root/literary-clock.log"
    : > "$runtime_root/kron/kron.log"
fi

start_epoch=$(date +%s)
# End shortly after the final regular cron boundary, while the Kindle is still
# awake from that job. A free-running stop timestamp can fall into powerd's
# unreliable ~60 second RTC window and displace the regular cadence wake.
minute_now=$(date +%M)
second_now=$(date +%S)
minute_value=$(awk -v value="$minute_now" 'BEGIN { print value + 0 }')
second_value=$(awk -v value="$second_now" 'BEGIN { print value + 0 }')
minute_remainder=$((minute_value % cadence))
if test "$minute_remainder" -eq 0; then
    minutes_to_first=$cadence
else
    minutes_to_first=$((cadence - minute_remainder))
fi
first_boundary=$((start_epoch - second_value + minutes_to_first * 60))
last_boundary=$((first_boundary + (cycles - 1) * cadence * 60))
stop_epoch=$((last_boundary + 45))
duration=$((stop_epoch - start_epoch))
stop_at=$(date -d "@$stop_epoch" '+%Y-%m-%d %H:%M:%S') || { echo "cannot compute bounded stop time" >&2; exit 60; }
original_prevent=$(lipc-get-prop com.lab126.powerd preventScreenSaver) || exit 61
awesome_pids=$(pidof awesome 2>/dev/null || true)
cvm_pids=$(pidof cvm 2>/dev/null || true)
battery_before=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null || echo unknown)
frontlight_before=$(lipc-get-prop com.lab126.powerd flIntensity 2>/dev/null || echo unknown)
wireless_before=$(lipc-get-prop com.lab126.cmd wirelessEnable 2>/dev/null || echo unknown)

: > "$status"
: > "$events"
{
    echo "study_start_epoch=$start_epoch"
    echo "release=$release"
    echo "strategy=$strategy"
    echo "cadence_minutes=$cadence"
    echo "cycles=$cycles"
    echo "first_scheduled_epoch=$first_boundary"
    echo "last_scheduled_epoch=$last_boundary"
    echo "bounded_stop=$stop_at"
    echo "state_location=$state_location"
    echo "full_refresh_interval=$refresh_interval"
    echo "keepawake=$keepawake"
    echo "reset_test_state=$reset_test_state"
    echo "prevent_before=$original_prevent"
    echo "battery_before=$battery_before"
    echo "frontlight_before=$frontlight_before"
    echo "wireless_before=$wireless_before"
} >> "$status"

{
    printf 'original_prevent\t%s\n' "$original_prevent"
    printf 'awesome_pids\t%s\n' "$awesome_pids"
    printf 'cvm_pids\t%s\n' "$cvm_pids"
    printf 'strategy\t%s\n' "$strategy"
    printf 'start_epoch\t%s\n' "$start_epoch"
    printf 'battery_before\t%s\n' "$battery_before"
    printf 'frontlight_before\t%s\n' "$frontlight_before"
    printf 'wireless_before\t%s\n' "$wireless_before"
} > "$session.new"
mv -f "$session.new" "$session"

case "$strategy" in
    *-prevent) target_prevent=1 ;;
    *) target_prevent=0 ;;
esac
lipc-set-prop com.lab126.powerd preventScreenSaver "$target_prevent" || exit 62
case "$strategy" in
    pause-ui | pause-ui-prevent)
        if test -n "$awesome_pids"; then kill -STOP $awesome_pids; fi
        if test -n "$cvm_pids"; then kill -STOP $cvm_pids; fi
        ;;
esac

event_pid=
if command -v lipc-wait-event >/dev/null 2>&1; then
    lipc-wait-event -m -t -s "$duration" com.lab126.powerd '*' >> "$events" 2>&1 &
    event_pid=$!
    printf 'event_pid\t%s\n' "$event_pid" >> "$session"
fi

export LITCLOCK_STATE_ROOT=$state_root
export LITCLOCK_FULL_REFRESH_INTERVAL=$refresh_interval
export LITCLOCK_SCHEDULER_SOURCE=kron-$cadence-minute
"$kron" -dir "$runtime_root/kron" remove literary-clock-study >/dev/null 2>&1 || true
"$kron" -dir "$runtime_root/kron" remove literary-clock-study-stop >/dev/null 2>&1 || true
"$kron" -dir "$runtime_root/kron" add -timeout 30s literary-clock-study "$schedule" "$launcher" || { "$stop_script"; exit 63; }
"$kron" -dir "$runtime_root/kron" add -timeout 60s literary-clock-study-stop "once $stop_at" "$stop_script" || { "$stop_script"; exit 64; }
"$kron" -dir "$runtime_root/kron" -keepawake "$keepawake" -wakelead 5s -jobtimeout 30s daemon >> "$status" 2>&1 &
kron_pid=$!
printf 'kron_pid\t%s\n' "$kron_pid" >> "$session"
sleep 2
if ! kill -0 "$kron_pid" 2>/dev/null; then
    wait "$kron_pid" 2>/dev/null || true
    "$stop_script" >/dev/null 2>&1 || true
    echo "KindleCron scheduler failed to remain running" >&2
    exit 67
fi
"$launcher" >> "$status" 2>&1 || { "$stop_script"; exit 65; }

# Independent bounded recovery. It does not poll; KindleCron remains the primary stop path.
( sleep "$duration"; "$stop_script" ) >/dev/null 2>&1 &
watchdog_pid=$!
printf 'watchdog_pid\t%s\n' "$watchdog_pid" >> "$session"
sync
setup_complete=1
trap - 0 1 2 15
echo "power study started; automatic restore scheduled for $stop_at"
