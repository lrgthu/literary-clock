#!/bin/sh
# Append one lightweight Phase 4D appliance snapshot on the Kindle.

set -u

runtime=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
qualification=${LITCLOCK_QUALIFICATION_ROOT:-$runtime/phase4d}
state=${LITCLOCK_STATE_ROOT:-/var/local/literary-clock/state}/state.tsv
service_log=$qualification/service/literary-clock.log
output=$qualification/checkpoints.tsv

mkdir -p "$qualification"
snapshot=$(date '+%s|%Y-%m-%d|%H:%M:%S|%Z%z') || exit 1
old_ifs=$IFS
IFS='|'
set -- $snapshot
IFS=$old_ifs
epoch=$1
local_date=$2
local_time=$3
timezone=$4

release=$(sed -n '1p' "$runtime/current-release" 2>/dev/null || echo missing)
engine=$(awk -F '\t' '$1 == "runtime_engine" { print $2; exit }' \
    "$runtime/releases/$release/release.meta" 2>/dev/null || echo missing)
display_count=$(awk -F '\t' '$1 == "DISPLAY_COUNT" { print $2; exit }' "$state" 2>/dev/null || echo 0)
history_rows=$(awk -F '\t' '$1 == "H" { count++ } END { print count + 0 }' "$state" 2>/dev/null || echo 0)
last_minute=$(awk -F '\t' '$1 == "LAST" { print $3; exit }' "$state" 2>/dev/null || echo none)
last_quote=$(awk -F '\t' '$1 == "LAST" { print $5; exit }' "$state" 2>/dev/null || echo none)
state_bytes=$(wc -c < "$state" 2>/dev/null || echo 0)
log_bytes=$(wc -c < "$service_log" 2>/dev/null || echo 0)
free_kb=$(df -k /mnt/us 2>/dev/null | tail -1 | awk '{ print $4 }')
battery=$(lipc-get-prop com.lab126.powerd battLevel 2>/dev/null || echo unknown)
frontlight=$(lipc-get-prop com.lab126.powerd flIntensity 2>/dev/null || echo unknown)
wireless=$(lipc-get-prop com.lab126.cmd wirelessEnable 2>/dev/null || echo unknown)
kron_pid=$(pidof kron 2>/dev/null || echo none)
case "$kron_pid" in
    '' | *[!0-9]*) scheduler_alive=0 ;;
    *) if kill -0 "$kron_pid" 2>/dev/null; then scheduler_alive=1; else scheduler_alive=0; fi ;;
esac
failures=$(awk '/display_result=(failed|error)/ { count++ } END { print count + 0 }' \
    "$service_log" 2>/dev/null || echo 0)

if ! test -s "$output"; then
    printf '%s\n' 'epoch	local_date	local_time	timezone	release	engine	display_count	state_bytes	history_rows	log_bytes	free_kb	battery	frontlight	wireless	scheduler_alive	last_minute	last_quote	failures' > "$output"
fi
printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$epoch" "$local_date" "$local_time" "$timezone" "$release" "$engine" \
    "$display_count" "$state_bytes" "$history_rows" "$log_bytes" "$free_kb" \
    "$battery" "$frontlight" "$wireless" "$scheduler_alive" "$last_minute" \
    "$last_quote" "$failures" >> "$output"
sync
