#!/bin/sh
# Name: Phase 4D Shell Native Roundtrip
# Author: Literary Clock
# DontUseFBInk

runtime=/mnt/us/literary-clock/runtime
phase=$runtime/phase4d
launcher=$runtime/literary-clock-launch-current.sh
state=/var/local/literary-clock/state/state.tsv
result=$phase/post25h-roundtrip.log
events=$phase/post25h-roundtrip-events.log

exec > "$result" 2>&1
cp "$state" "$phase/state-before-fallback.tsv" || exit 1
echo "started=$(date '+%s|%Y-%m-%d|%H%M|%Z%z')"
echo "before=$(awk -F '\t' '$1 == "DISPLAY_COUNT" { print $2; exit }' "$state")"

export LITCLOCK_RUNTIME_ENGINE=shell
export LITCLOCK_STATE_ROOT=/var/local/literary-clock/state
export LITCLOCK_LOG_FILE=$events
export LITCLOCK_SCHEDULER_SOURCE=phase4d-shell-fallback
"$launcher" || exit 2
echo "after_shell=$(awk -F '\t' '$1 == "DISPLAY_COUNT" { print $2; exit }' "$state")"

second=$(date +%S)
second=${second#0}
test -n "$second" || second=0
sleep $((61 - second))

export LITCLOCK_RUNTIME_ENGINE=native
export LITCLOCK_SCHEDULER_SOURCE=phase4d-native-return
"$launcher" || exit 3
echo "after_native=$(awk -F '\t' '$1 == "DISPLAY_COUNT" { print $2; exit }' "$state")"
cp "$state" "$phase/state-after-roundtrip.tsv" || exit 4
echo "finished=$(date '+%s|%Y-%m-%d|%H%M|%Z%z')"
sync
