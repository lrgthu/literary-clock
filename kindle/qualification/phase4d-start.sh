#!/bin/sh
# Start the bounded 25-hour native appliance qualification.

set -u

runtime=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
qualification=$runtime/phase4d
checkpoint=$qualification/phase4d-checkpoint.sh
kron=$runtime/tools/kron
test -x "$kron" || kron=$runtime/bin/kron
test -x "$checkpoint" || exit 1

mkdir -p "$qualification/service"
: > "$qualification/checkpoints.tsv"
: > "$qualification/service/literary-clock.log"
: > "$qualification/start.log"
exec >> "$qualification/start.log" 2>&1

echo "qualification_prepare=$(date '+%s|%Y-%m-%d|%H:%M:%S|%Z%z')"
"$checkpoint"

for name in phase4d-1h phase4d-6h phase4d-12h phase4d-18h phase4d-24h phase4d-25h; do
    "$kron" -dir "$runtime/kron" remove "$name" >/dev/null 2>&1 || true
done

start_epoch=$(date +%s)
for row in 'phase4d-1h 3600' 'phase4d-6h 21600' 'phase4d-12h 43200' \
    'phase4d-18h 64800' 'phase4d-24h 86400' 'phase4d-25h 90000'; do
    set -- $row
    target_epoch=$((start_epoch + $2 + 20))
    target=$(date -d "@$target_epoch" '+%Y-%m-%d %H:%M:%S') || exit 2
    "$kron" -dir "$runtime/kron" add -timeout 30s "$1" "once $target" "$checkpoint" || exit 3
done

export LITCLOCK_SERVICE_ROOT=$qualification/service
export LITCLOCK_STATE_ROOT=/var/local/literary-clock/state
export LITCLOCK_QUALIFICATION_ROOT=$qualification
"$runtime/literary-clock-service-start-current.sh" || exit 4
echo "qualification_started=$(date '+%s|%Y-%m-%d|%H:%M:%S|%Z%z')"
"$checkpoint"
sync
