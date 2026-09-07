#!/bin/sh
# One-shot, Kindle-local Literary Clock selector and display transaction.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
script_dir=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
release_root=${LITCLOCK_RELEASE_ROOT:-$(dirname "$script_dir")}
bundle=$release_root/bundle
release_meta=$release_root/release.meta
state_dir=${LITCLOCK_STATE_ROOT:-$runtime_root/state}
state_file=$state_dir/state.tsv
log_file=${LITCLOCK_LOG_FILE:-$runtime_root/literary-clock.log}
display_helper=${LITCLOCK_DISPLAY_HELPER:-$release_root/bin/literary-clock-display.sh}
lock_dir=$state_dir/run.lock
full_refresh_interval=${LITCLOCK_FULL_REFRESH_INTERVAL:-15}
scheduler_source=${LITCLOCK_SCHEDULER_SOURCE:-manual}

# New native releases dispatch here so the stable current-release launcher and
# scheduler lifecycle remain unchanged.  LITCLOCK_RUNTIME_ENGINE=shell is the
# explicit, state-preserving recovery switch.
runtime_engine=${LITCLOCK_RUNTIME_ENGINE:-}
if test -z "$runtime_engine" && test -r "$release_meta"; then
    runtime_engine=$(awk -F '\t' '$1 == "runtime_engine" { print $2; exit }' "$release_meta")
fi
if test -z "$runtime_engine"; then runtime_engine=shell; fi
case "$runtime_engine" in
    native)
        native_runtime=$release_root/bin/litclock-native
        test -x "$native_runtime" || {
            echo "Literary Clock native runtime is missing" >&2
            exit 24
        }
        exec "$native_runtime" "$@"
        ;;
    shell) ;;
    *)
        echo "Literary Clock runtime engine is invalid: $runtime_engine" >&2
        exit 24
        ;;
esac

clock_ms() {
    if test -r /proc/uptime; then
        awk '{ printf "%.0f\n", $1 * 1000 }' /proc/uptime
    else
        seconds=$(date +%s)
        echo $((seconds * 1000))
    fi
}

power_state() {
    if command -v lipc-get-prop >/dev/null 2>&1; then
        lipc-get-prop com.lab126.powerd state 2>/dev/null || echo unknown
    else
        echo unavailable
    fi
}

log_line() {
    mkdir -p "$(dirname "$log_file")"
    if test -f "$log_file" && test "$(wc -c < "$log_file")" -gt 262144; then
        tail -n 1000 "$log_file" > "$log_file.tmp" && mv -f "$log_file.tmp" "$log_file"
    fi
    printf '%s\n' "$*" >> "$log_file"
}

die() {
    log_line "timestamp=${epoch:-0} local_date=${iso_date:-none} minute=${minute_key:-none} quote_id=${selected_id:-none} display_count=${next_display_count:-none} display_result=error history_commit=no error=$1"
    echo "$1" >&2
    exit "${2:-1}"
}

cleanup() {
    rm -f "$lock_dir/pid" 2>/dev/null || true
    rmdir "$lock_dir" 2>/dev/null || true
}

active_child_pid=

on_signal() {
    code=$1
    signal_name=$2
    trap - 0 1 2 15
    if test -n "$active_child_pid"; then
        kill -TERM "$active_child_pid" 2>/dev/null || true
        wait "$active_child_pid" 2>/dev/null || true
        active_child_pid=
    fi
    log_line "timestamp=${epoch:-0} local_date=${iso_date:-none} minute=${minute_key:-none} quote_id=${selected_id:-none} display_count=${next_display_count:-none} display_result=signal-$signal_name history_commit=no"
    cleanup
    exit "$code"
}

run_interruptible() {
    "$@" &
    active_child_pid=$!
    wait "$active_child_pid"
    child_rc=$?
    active_child_pid=
    return "$child_rc"
}

mkdir -p "$state_dir"
if ! mkdir "$lock_dir" 2>/dev/null; then
    old_pid=$(sed -n '1p' "$lock_dir/pid" 2>/dev/null || true)
    if test -n "$old_pid" && kill -0 "$old_pid" 2>/dev/null; then
        die "another Literary Clock update is active" 20
    fi
    rm -f "$lock_dir/pid" 2>/dev/null || true
    rmdir "$lock_dir" 2>/dev/null || die "stale runtime lock cannot be recovered" 21
    mkdir "$lock_dir" 2>/dev/null || die "runtime lock acquisition failed" 22
fi
printf '%s\n' "$$" > "$lock_dir/pid"
trap cleanup 0
trap 'on_signal 129 HUP' 1
trap 'on_signal 130 INT' 2
trap 'on_signal 143 TERM' 15

case "$full_refresh_interval" in
    "" | *[!0-9]*) die "full refresh interval is invalid" 23 ;;
esac
test "$full_refresh_interval" -ge 1 || die "full refresh interval is invalid" 23

test -r "$release_meta" || die "release metadata is missing" 24
meta_value() {
    awk -F '\t' -v key="$1" '$1 == key { print $2; exit }' "$2"
}
release_format=$(meta_value release_format_version "$release_meta")
runtime_version=$(meta_value runtime_version "$release_meta")
release_version=$(meta_value release_version "$release_meta")
test "$release_format" = 1 || die "release format is incompatible" 25
test "$runtime_version" = 2 || die "runtime version is incompatible" 26
case "$release_version" in
    "" | */* | *..*) die "release version is invalid" 27 ;;
esac
for required in bundle.meta minutes.tsv quotes.tsv dates.tsv; do
    test -r "$bundle/$required" || die "bundle index is missing: $required" 28
done
test "$(meta_value format_version "$bundle/bundle.meta")" = 1 || die "manifest format is incompatible" 29
test "$(meta_value release_format_version "$bundle/bundle.meta")" = "$release_format" || die "release/bundle format mismatch" 30
test "$(meta_value runtime_version "$bundle/bundle.meta")" = "$runtime_version" || die "release/bundle runtime mismatch" 31

total_started=$(clock_ms)
power_before=$(power_state)

# One and only one local-time capture supplies epoch, calendar date, minute and date key.
snapshot=${LITCLOCK_TIMESTAMP_SNAPSHOT:-}
if test -z "$snapshot"; then
    snapshot=$(date '+%s|%Y-%m-%d|%H%M|%w-%m-%d') || die "local timestamp capture failed" 32
fi
old_ifs=$IFS
IFS='|'
set -- $snapshot
IFS=$old_ifs
test "$#" -eq 4 || die "timestamp snapshot is malformed" 33
epoch=$1
iso_date=$2
hhmm=$3
date_key=$4
case "$epoch:$iso_date:$hhmm:$date_key" in
    *[!0-9:-]*) die "timestamp snapshot contains invalid characters" 34 ;;
esac
test "${#hhmm}" -eq 4 || die "timestamp minute is malformed" 35
hour=$(printf '%s' "$hhmm" | cut -c 1-2)
minute=$(printf '%s' "$hhmm" | cut -c 3-4)
minute_number=$(awk -v h="$hour" -v m="$minute" 'BEGIN { print (h + 0) * 60 + (m + 0) }')
test "$minute_number" -ge 0 2>/dev/null && test "$minute_number" -le 1439 2>/dev/null || die "timestamp minute is outside range" 36
minute_key=$(printf '%04d' "$minute_number")

if test -r "$state_file"; then
    last_value=$(awk -F '\t' '$1 == "LAST" { print $2 "|" $3 "|" $6; exit }' "$state_file")
    if test "$last_value" = "$iso_date|$minute_key|$release_version"; then
        log_line "timestamp=$epoch local_date=$iso_date minute=$minute_key quote_id=none display_count=unchanged selection_reason=already-current display_result=skipped refresh_mode=none history_commit=no duration_ms=0 power_state_before=$power_before power_state_after=$(power_state) scheduler_source=$scheduler_source"
        exit 0
    fi
fi

display_count=0
state_version=1
if test -r "$state_file"; then
    stored_version=$(awk -F '\t' '$1 == "VERSION" { print $2; exit }' "$state_file")
    if test -n "$stored_version"; then state_version=$stored_version; fi
    case "$state_version" in
        1)
            display_count=$(awk -F '\t' '$1 == "H" { count++ } END { print count + 0 }' "$state_file")
            ;;
        2)
            display_count=$(awk -F '\t' '$1 == "DISPLAY_COUNT" { print $2; found = 1; exit } END { if (!found) print 0 }' "$state_file")
            ;;
        *) die "state version is incompatible" 37 ;;
    esac
fi
case "$display_count" in
    "" | *[!0-9]*) die "display counter is invalid" 38 ;;
esac
next_display_count=$((display_count + 1))

# Prefix fixed-width keys before comparing them. BusyBox awk otherwise treats
# values such as 0270 as octal (184), which can select the 03:04 pool at 04:30.
candidates=$(awk -F '\t' -v minute="$minute_key" '"m" $1 == "m" minute { print $2; exit }' "$bundle/minutes.tsv")
test -n "$candidates" || die "active bundle has no candidates for minute $minute_key" 39
remaining=
if test -r "$state_file"; then
    remaining=$(awk -F '\t' -v minute="$minute_key" '$1 == "BAG" && "m" $2 == "m" minute { print $3; exit }' "$state_file")
fi

if test -z "$remaining"; then
    shuffle_source=$state_dir/shuffle.$$.tsv
    old_ifs=$IFS
    IFS=','
    for quote_id in $candidates; do
        key=$(printf '%s' "$release_version:$epoch:$minute_key:$quote_id" | cksum | awk '{print $1}')
        printf '%010u\t%s\n' "$key" "$quote_id"
    done > "$shuffle_source"
    IFS=$old_ifs
    remaining=$(sort -n "$shuffle_source" | cut -f 2 | paste -sd, -)
    rm -f "$shuffle_source"
fi
test -n "$remaining" || die "shuffle bag initialization failed" 40

selection_state=$state_file
if ! test -r "$selection_state"; then selection_state=/dev/null; fi
selection=$(awk -F '\t' -v candidates="$remaining" -v now="$epoch" -v statefile="$selection_state" '
    FILENAME == statefile && $1 == "H" {
        if ($2 > last_quote[$5]) last_quote[$5] = $2
        if ($6 != "-" && $2 > last_book[$6]) last_book[$6] = $2
        if ($7 != "-" && $2 > last_author[$7]) last_author[$7] = $2
        next
    }
    FILENAME != statefile && NF >= 4 {
        frame[$1] = $2; book[$1] = $3; author[$1] = $4
    }
    END {
        count = split(candidates, ids, ",")
        for (level = 0; level <= 7; level++) {
            for (i = 1; i <= count; i++) {
                q = ids[i]
                if (!(q in frame)) continue
                qr = !(q in last_quote) || now - last_quote[q] >= 86400
                br = book[q] == "-" || !(book[q] in last_book) || now - last_book[book[q]] >= 43200
                ar = author[q] == "-" || !(author[q] in last_author) || now - last_author[author[q]] >= 21600
                ok = (level == 0 && qr && br && ar) ||
                     (level == 1 && qr && br) ||
                     (level == 2 && qr && ar) ||
                     (level == 3 && qr) ||
                     (level == 4 && br && ar) ||
                     (level == 5 && br) ||
                     (level == 6 && ar) || level == 7
                if (ok) { print q "\t" level "\t" frame[q] "\t" book[q] "\t" author[q]; exit }
            }
        }
    }
' "$selection_state" "$bundle/quotes.tsv" 2>/dev/null)
test -n "$selection" || die "no selectable quote metadata remains" 41
selected_id=$(printf '%s\n' "$selection" | cut -f 1)
selection_reason=$(printf '%s\n' "$selection" | cut -f 2)
frame_relative=$(printf '%s\n' "$selection" | cut -f 3)
book_id=$(printf '%s\n' "$selection" | cut -f 4)
author_id=$(printf '%s\n' "$selection" | cut -f 5)

date_row=$(awk -F '\t' -v key="$date_key" '$1 == key { print; exit }' "$bundle/dates.tsv")
test -n "$date_row" || die "bundle has no date overlay for $date_key" 42
date_relative=$(printf '%s\n' "$date_row" | cut -f 2)
date_x=$(printf '%s\n' "$date_row" | cut -f 3)
date_y=$(printf '%s\n' "$date_row" | cut -f 4)
frame=$bundle/$frame_relative
date_frame=$bundle/$date_relative

previous_date=
if test -r "$state_file"; then
    previous_date=$(awk -F '\t' '$1 == "LAST" { print $2; exit }' "$state_file")
fi
refresh_mode=GL16
if test "$display_count" -eq 0 || test "$previous_date" != "$iso_date" || test $((next_display_count % full_refresh_interval)) -eq 0; then
    refresh_mode=GC16
fi
selected_at=$(clock_ms)

test_delay=${LITCLOCK_TEST_DELAY_BEFORE_DISPLAY:-0}
if test "$test_delay" != 0; then
    run_interruptible sleep "$test_delay" || die "pre-display delay failed" 44
fi

display_started=$(clock_ms)
if run_interruptible "$display_helper" "$frame" "$date_frame" "$date_x" "$date_y" "$refresh_mode"; then
    display_rc=0
else
    display_rc=$?
fi
display_ended=$(clock_ms)
display_ms=$((display_ended - display_started))
if test "$display_rc" -ne 0; then
    total_ended=$(clock_ms)
    log_line "timestamp=$epoch local_date=$iso_date minute=$minute_key quote_id=$selected_id display_count=$display_count selection_reason=$selection_reason display_result=failed-$display_rc refresh_mode=$refresh_mode history_commit=no selector_ms=$((selected_at - total_started)) display_ms=$display_ms state_ms=0 duration_ms=$((total_ended - total_started)) power_state_before=$power_before power_state_after=$(power_state) scheduler_source=$scheduler_source"
    exit "$display_rc"
fi

test_delay=${LITCLOCK_TEST_DELAY_BEFORE_COMMIT:-0}
if test "$test_delay" != 0; then
    run_interruptible sleep "$test_delay" || die "pre-commit delay failed" 45
fi

new_remaining=$(printf '%s\n' "$remaining" | awk -F ',' -v selected="$selected_id" '{
    output = ""
    for (i = 1; i <= NF; i++) if ($i != selected) output = output (output ? "," : "") $i
    print output
}')
state_started=$(clock_ms)
state_tmp=$state_dir/state.$$.tmp
cutoff=$((epoch - 90000))
{
    printf 'VERSION\t2\n'
    printf 'DISPLAY_COUNT\t%s\n' "$next_display_count"
    if test -r "$state_file"; then
        awk -F '\t' -v minute="$minute_key" -v cutoff="$cutoff" '
            $1 == "H" && $2 >= cutoff { print; next }
            $1 == "BAG" && "m" $2 != "m" minute { print; next }
        ' "$state_file"
    fi
    printf 'H\t%s\t%s\t%s\t%s\t%s\t%s\n' "$epoch" "$iso_date" "$minute_key" "$selected_id" "$book_id" "$author_id"
    printf 'BAG\t%s\t%s\n' "$minute_key" "$new_remaining"
    printf 'LAST\t%s\t%s\t%s\t%s\t%s\n' "$iso_date" "$minute_key" "$epoch" "$selected_id" "$release_version"
} > "$state_tmp"
sync
mv -f "$state_tmp" "$state_file" || die "atomic history activation failed after display" 43
sync
state_ended=$(clock_ms)
total_ended=$(clock_ms)
power_after=$(power_state)
log_line "timestamp=$epoch local_date=$iso_date minute=$minute_key quote_id=$selected_id display_count=$next_display_count frame=$frame_relative selection_reason=$selection_reason display_result=success refresh_mode=$refresh_mode history_commit=yes selector_ms=$((selected_at - total_started)) display_ms=$display_ms state_ms=$((state_ended - state_started)) duration_ms=$((total_ended - total_started)) power_state_before=$power_before power_state_after=$power_after scheduler_source=$scheduler_source"
printf 'displayed minute=%s quote_id=%s count=%s refresh=%s duration_ms=%s\n' "$minute_key" "$selected_id" "$next_display_count" "$refresh_mode" "$((total_ended - total_started))"
