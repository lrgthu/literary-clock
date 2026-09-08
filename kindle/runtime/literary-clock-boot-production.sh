#!/bin/sh
# Persistent KMC framework_ready hook for the production appliance.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
launcher=$runtime_root/literary-clock-service-start-current.sh
log=$runtime_root/boot-start.log

test -x "$launcher" || exit 91

# KMC invokes this once per boot. Keep its diagnostic output bounded while the
# service launcher provides release validation and duplicate-process protection.
if test -f "$log" && test "$(wc -c < "$log" 2>/dev/null || echo 0)" -gt 262144; then
    tail -c 131072 "$log" > "$log.new" 2>/dev/null && mv -f "$log.new" "$log"
fi

{
    printf 'production_boot_epoch=%s\n' "$(date +%s)"
    "$launcher"
} >> "$log" 2>&1
