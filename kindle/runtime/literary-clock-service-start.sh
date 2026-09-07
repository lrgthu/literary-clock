#!/bin/sh
# Start the reversible exact-minute Literary Clock service.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
service_root=${LITCLOCK_SERVICE_ROOT:-/var/local/literary-clock/service}
state_root=${LITCLOCK_STATE_ROOT:-/var/local/literary-clock/state}
config=${LITCLOCK_SERVICE_CONFIG:-$runtime_root/service.conf}
session=$service_root/session.tsv
status=$service_root/status.txt
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
release_root=${LITCLOCK_RELEASE_ROOT:-$(dirname "$script_dir")}
release=$(awk -F '\t' '$1 == "release_version" { print $2; exit }' "$release_root/release.meta" 2>/dev/null)
kron=${LITCLOCK_KRON:-$runtime_root/tools/kron}
if ! test -x "$kron"; then kron=$runtime_root/bin/kron; fi
launcher=${LITCLOCK_CURRENT_LAUNCHER:-$runtime_root/literary-clock-launch-current.sh}
stop_script=$release_root/bin/literary-clock-service-stop.sh
validator=$release_root/bin/literary-clock-validate-release.sh
setup_complete=0

read_config() {
    value=$(awk -F '=' -v key="$1" '$1 == key { print $2; exit }' "$config" 2>/dev/null)
    if test -n "$value"; then echo "$value"; else echo "$2"; fi
}

process_is() {
    pid=$1
    expected=$2
    if test "${LITCLOCK_TEST_ASSUME_PROCESS:-0}" = 1; then
        kill -0 "$pid" 2>/dev/null
        return
    fi
    test -r "/proc/$pid/cmdline" || return 1
    tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "$expected"
}

abort_setup() {
    rc=$?
    trap - 0 1 2 15
    if test "$setup_complete" -eq 0 && test -r "$session" && test -x "$stop_script"; then
        LITCLOCK_RELEASE_ROOT="$release_root" "$stop_script" >/dev/null 2>&1 || true
    fi
    exit "$rc"
}

trap abort_setup 0 1 2 15

mkdir -p "$service_root" "$state_root"
test -n "$release" || { echo "active release metadata is invalid" >&2; exit 70; }
test -x "$kron" || { echo "KindleCron is unavailable" >&2; exit 71; }
test -x "$launcher" || { echo "current-release launcher is unavailable" >&2; exit 72; }
test -x "$stop_script" && test -x "$validator" || { echo "release service helpers are missing" >&2; exit 73; }

if test -r "$session"; then
    old_pid=$(awk -F '\t' '$1 == "kron_pid" { print $2; exit }' "$session")
    if test -n "$old_pid" && process_is "$old_pid" kron; then
        echo "Literary Clock service is already active"
        setup_complete=1
        trap - 0 1 2 15
        exit 0
    fi
    LITCLOCK_RELEASE_ROOT="$release_root" "$stop_script" >/dev/null 2>&1 || true
fi

cadence=$(read_config cadence_minutes 1)
keepawake=$(read_config keepawake 3m)
full_refresh_interval=$(read_config full_refresh_interval 15)
auto_stop_minutes=$(read_config auto_stop_minutes 0)
case "$cadence" in
    1) schedule='* * * * *' ;;
    2 | 3 | 5) schedule="*/$cadence * * * *" ;;
    *) echo "cadence_minutes must be 1, 2, 3, or 5" >&2; exit 74 ;;
esac
case "$keepawake" in
    off) ;;
    *[smh])
        keepawake_value=${keepawake%?}
        case "$keepawake_value" in "" | *[!0-9]*) echo "invalid keepawake duration" >&2; exit 75 ;; esac
        test "$keepawake_value" -ge 1 || { echo "invalid keepawake duration" >&2; exit 75; }
        ;;
    *) echo "keepawake must be 'off' or a duration" >&2; exit 75 ;;
esac
case "$full_refresh_interval:$auto_stop_minutes" in
    *[!0-9:]*) echo "refresh interval and auto-stop must be integers" >&2; exit 76 ;;
esac
test "$full_refresh_interval" -ge 1 || { echo "full_refresh_interval must be positive" >&2; exit 76; }
test "$auto_stop_minutes" -ge 0 || { echo "auto_stop_minutes must not be negative" >&2; exit 76; }

clock_attempts=0
while :; do
    year=$(date +%Y 2>/dev/null || echo 0)
    case "$year" in "" | *[!0-9]*) year=0 ;; esac
    if test "$year" -ge 2024; then break; fi
    clock_attempts=$((clock_attempts + 1))
    test "$clock_attempts" -lt 30 || { echo "Kindle local clock is not sane" >&2; exit 78; }
    sleep 2
done
validation_started=$(date +%s)
"$validator" "$release_root" || exit 77
validation_ended=$(date +%s)
: > "$status"
{
    echo "release=$release"
    echo "cadence_minutes=$cadence"
    echo "keepawake=$keepawake"
    echo "full_refresh_interval=$full_refresh_interval"
    echo "auto_stop_minutes=$auto_stop_minutes"
    echo "release_validation_seconds=$((validation_ended - validation_started))"
} >> "$status"

# Stop only the daemon in our dedicated data directory. KindleCron itself
# validates its lock before signalling, so a stale PID cannot be targeted.
"$kron" -dir "$runtime_root/kron" remove literary-clock-minute >/dev/null 2>&1 || true
"$kron" -dir "$runtime_root/kron" remove literary-clock-service-stop >/dev/null 2>&1 || true
"$kron" -dir "$runtime_root/kron" stop >/dev/null 2>&1 || true

original_prevent=$(lipc-get-prop com.lab126.powerd preventScreenSaver 2>/dev/null || echo 0)
case "$original_prevent" in 0 | 1) ;; *) original_prevent=0 ;; esac
boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)
started_epoch=$(date +%s)
{
    printf 'format_version\t1\n'
    printf 'release\t%s\n' "$release"
    printf 'boot_id\t%s\n' "$boot_id"
    printf 'original_prevent\t%s\n' "$original_prevent"
    printf 'cadence_minutes\t%s\n' "$cadence"
    printf 'keepawake\t%s\n' "$keepawake"
    printf 'started_epoch\t%s\n' "$started_epoch"
} > "$session.new"
mv -f "$session.new" "$session"

export LITCLOCK_STATE_ROOT=$state_root
export LITCLOCK_LOG_FILE=$service_root/literary-clock.log
export LITCLOCK_FULL_REFRESH_INTERVAL=$full_refresh_interval
export LITCLOCK_SCHEDULER_SOURCE=kron-service-$cadence-minute
"$kron" -dir "$runtime_root/kron" add -timeout 30s literary-clock-minute "$schedule" "$launcher" || exit 79

if test "$auto_stop_minutes" -gt 0; then
    stop_epoch=$((started_epoch + auto_stop_minutes * 60))
    stop_at=$(date -d "@$stop_epoch" '+%Y-%m-%d %H:%M:%S') || exit 80
    "$kron" -dir "$runtime_root/kron" add -timeout 60s literary-clock-service-stop \
        "once $stop_at" "$stop_script" || exit 81
    printf 'auto_stop_epoch\t%s\n' "$stop_epoch" >> "$session"
fi

"$kron" -dir "$runtime_root/kron" -logmax 256 -keepawake "$keepawake" -wakelead 5s \
    -jobtimeout 30s daemon >> "$status" 2>&1 &
kron_pid=$!
printf 'kron_pid\t%s\n' "$kron_pid" >> "$session"
sleep 2
if ! kill -0 "$kron_pid" 2>/dev/null || ! process_is "$kron_pid" kron; then
    echo "KindleCron scheduler failed to remain running" >&2
    exit 82
fi

# Change visible framework state only after assets and scheduler are known good.
lipc-set-prop com.lab126.powerd preventScreenSaver 0 >/dev/null 2>&1 || exit 83
for name in awesome cvm; do
    pids=$(pidof "$name" 2>/dev/null || true)
    if test -n "$pids"; then kill -STOP $pids || exit 84; fi
done

"$launcher" >> "$status" 2>&1 || exit 85
{
    printf 'active\tyes\n'
    printf 'initial_display_epoch\t%s\n' "$(date +%s)"
} >> "$session"
setup_complete=1
trap - 0 1 2 15
echo "Literary Clock exact service started (release $release, cadence ${cadence}m)"
