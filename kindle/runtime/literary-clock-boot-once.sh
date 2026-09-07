#!/bin/sh
# One-shot KMC framework_ready hook for a controlled reboot test.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
hook=${LITCLOCK_BOOT_HOOK:-/mnt/us/emergency.sh}
used=$runtime_root/emergency.sh.used
log=$runtime_root/boot-start.log
launcher=$runtime_root/literary-clock-service-start-current.sh

# Disarm before starting anything. A failed launch must not repeat next boot.
test -f "$hook" || exit 90
test -x "$launcher" || exit 91
mv -f "$hook" "$used" || exit 92
sync

exec "$launcher" >> "$log" 2>&1
