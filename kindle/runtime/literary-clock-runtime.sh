#!/bin/sh
# One-shot, Kindle-local Literary Clock selector and display transaction.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
bundle_base=${LITCLOCK_BUNDLE_BASE:-$runtime_root/bundles}
pointer=${LITCLOCK_CURRENT_POINTER:-$runtime_root/current}
state_dir=${LITCLOCK_STATE_DIR:-$runtime_root/state}
state_file=$state_dir/state.tsv
log_file=${LITCLOCK_LOG_FILE:-$runtime_root/literary-clock.log}
display_helper=${LITCLOCK_DISPLAY_HELPER:-$runtime_root/bin/literary-clock-display.sh}
lock_dir=$state_dir/run.lock

log_line() {
    mkdir -p "$(dirname "$log_file")"
    if test -f "$log_file" && test "$(wc -c < "$log_file")" -gt 262144; then
        tail -n 1000 "$log_file" > "$log_file.tmp" && mv -f "$log_file.tmp" "$log_file"
    fi
    printf '%s\n' "$*" >> "$log_file"
}

die() {
    log_line "timestamp=${epoch:-0} minute=${minute_key:-none} quote_id=${selected_id:-none} display_result=error history_commit=no error=$1"
    echo "$1" >&2
    exit "${2:-1}"
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
cleanup() {
    rm -f "$lock_dir/pid" 2>/dev/null || true
    rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup 0 1 2 15

test -r "$pointer" || die "active bundle pointer is missing" 23
bundle_version=$(sed -n '1p' "$pointer")
case "$bundle_version" in
    "" | */* | *..*) die "active bundle pointer is invalid" 24 ;;
esac
bundle=$bundle_base/$bundle_version
test -d "$bundle" || die "active bundle directory is missing" 25
for required in bundle.meta minutes.tsv quotes.tsv dates.tsv; do
    test -r "$bundle/$required" || die "bundle index is missing: $required" 26
done

# One and only one local-time capture supplies epoch, calendar date, minute and date key.
snapshot=${LITCLOCK_TIMESTAMP_SNAPSHOT:-}
if test -z "$snapshot"; then
    snapshot=$(date '+%s|%Y-%m-%d|%H%M|%w-%m-%d') || die "local timestamp capture failed" 27
fi
old_ifs=$IFS
IFS='|'
set -- $snapshot
IFS=$old_ifs
test "$#" -eq 4 || die "timestamp snapshot is malformed" 28
epoch=$1
iso_date=$2
hhmm=$3
date_key=$4
case "$epoch:$iso_date:$hhmm:$date_key" in
    *[!0-9:-]*) die "timestamp snapshot contains invalid characters" 29 ;;
esac
test "${#hhmm}" -eq 4 || die "timestamp minute is malformed" 30
hour=$(printf '%s' "$hhmm" | cut -c 1-2)
minute=$(printf '%s' "$hhmm" | cut -c 3-4)
minute_number=$(awk -v h="$hour" -v m="$minute" 'BEGIN { print (h + 0) * 60 + (m + 0) }')
test "$minute_number" -ge 0 2>/dev/null && test "$minute_number" -le 1439 2>/dev/null || die "timestamp minute is outside range" 31
minute_key=$(printf '%04d' "$minute_number")

if test -r "$state_file"; then
    last_value=$(awk -F '\t' '$1 == "LAST" { print $2 "|" $3 "|" $6; exit }' "$state_file")
    if test "$last_value" = "$iso_date|$minute_key|$bundle_version"; then
        log_line "timestamp=$epoch minute=$minute_key quote_id=none selection_reason=already-current display_result=skipped refresh_mode=none history_commit=no duration_ms=0"
        exit 0
    fi
fi

candidates=$(awk -F '\t' -v minute="$minute_key" '$1 == minute { print $2; exit }' "$bundle/minutes.tsv")
test -n "$candidates" || die "active bundle has no candidates for minute $minute_key" 32
remaining=
if test -r "$state_file"; then
    remaining=$(awk -F '\t' -v minute="$minute_key" '$1 == "BAG" && $2 == minute { print $3; exit }' "$state_file")
fi

if test -z "$remaining"; then
    shuffle_source=$state_dir/shuffle.$$.tsv
    old_ifs=$IFS
    IFS=','
    for quote_id in $candidates; do
        key=$(printf '%s' "$bundle_version:$epoch:$minute_key:$quote_id" | cksum | awk '{print $1}')
        printf '%010u\t%s\n' "$key" "$quote_id"
    done > "$shuffle_source"
    IFS=$old_ifs
    remaining=$(sort -n "$shuffle_source" | cut -f 2 | paste -sd, -)
    rm -f "$shuffle_source"
fi
test -n "$remaining" || die "shuffle bag initialization failed" 33

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
test -n "$selection" || die "no selectable quote metadata remains" 34
selected_id=$(printf '%s\n' "$selection" | cut -f 1)
selection_reason=$(printf '%s\n' "$selection" | cut -f 2)
frame_relative=$(printf '%s\n' "$selection" | cut -f 3)
book_id=$(printf '%s\n' "$selection" | cut -f 4)
author_id=$(printf '%s\n' "$selection" | cut -f 5)

date_row=$(awk -F '\t' -v key="$date_key" '$1 == key { print; exit }' "$bundle/dates.tsv")
test -n "$date_row" || die "bundle has no date overlay for $date_key" 35
date_relative=$(printf '%s\n' "$date_row" | cut -f 2)
date_x=$(printf '%s\n' "$date_row" | cut -f 3)
date_y=$(printf '%s\n' "$date_row" | cut -f 4)
frame=$bundle/$frame_relative
date_frame=$bundle/$date_relative

history_count=0
previous_date=
if test -r "$state_file"; then
    history_count=$(awk -F '\t' '$1 == "H" { count++ } END { print count + 0 }' "$state_file")
    previous_date=$(awk -F '\t' '$1 == "LAST" { print $2; exit }' "$state_file")
fi
refresh_mode=GL16
if test "$history_count" -eq 0 || test "$previous_date" != "$iso_date" || test $((history_count % 15)) -eq 0; then
    refresh_mode=GC16
fi

started=$(date +%s)
"$display_helper" "$frame" "$date_frame" "$date_x" "$date_y" "$refresh_mode"
display_rc=$?
if test "$display_rc" -ne 0; then
    ended=$(date +%s)
    duration_ms=$(((ended - started) * 1000))
    log_line "timestamp=$epoch minute=$minute_key quote_id=$selected_id selection_reason=$selection_reason display_result=failed-$display_rc refresh_mode=$refresh_mode history_commit=no duration_ms=$duration_ms"
    exit "$display_rc"
fi

new_remaining=$(printf '%s\n' "$remaining" | awk -F ',' -v selected="$selected_id" '{
    output = ""
    for (i = 1; i <= NF; i++) if ($i != selected) output = output (output ? "," : "") $i
    print output
}')
state_tmp=$state_dir/state.$$.tmp
cutoff=$((epoch - 90000))
if test -r "$state_file"; then
    awk -F '\t' -v minute="$minute_key" -v cutoff="$cutoff" '
        $1 == "H" && $2 >= cutoff { print; next }
        $1 == "BAG" && $2 != minute { print; next }
        $1 == "VERSION" { print; next }
    ' "$state_file" > "$state_tmp"
else
    : > "$state_tmp"
fi
if ! awk -F '\t' '$1 == "VERSION" { found = 1 } END { exit !found }' "$state_tmp"; then
    printf 'VERSION\t1\n' >> "$state_tmp"
fi
printf 'H\t%s\t%s\t%s\t%s\t%s\t%s\n' "$epoch" "$iso_date" "$minute_key" "$selected_id" "$book_id" "$author_id" >> "$state_tmp"
printf 'BAG\t%s\t%s\n' "$minute_key" "$new_remaining" >> "$state_tmp"
printf 'LAST\t%s\t%s\t%s\t%s\t%s\n' "$iso_date" "$minute_key" "$epoch" "$selected_id" "$bundle_version" >> "$state_tmp"
sync
mv -f "$state_tmp" "$state_file" || die "atomic history activation failed after display" 36
sync

ended=$(date +%s)
duration_ms=$(((ended - started) * 1000))
log_line "timestamp=$epoch minute=$minute_key quote_id=$selected_id frame=$frame_relative selection_reason=$selection_reason display_result=success refresh_mode=$refresh_mode history_commit=yes duration_ms=$duration_ms"
printf 'displayed minute=%s quote_id=%s refresh=%s duration_ms=%s\n' "$minute_key" "$selected_id" "$refresh_mode" "$duration_ms"
