#!/bin/sh
# Name: Literary Clock Phase 4B.2 Runtime Audit
# Author: Literary Clock
# DontUseFBInk

# Read-only audit of the installed startup and durability facilities.
set -u

output=/mnt/us/literary-clock/phase4b2-runtime-audit.txt
exec > "$output" 2>&1

section() { printf '\n--- %s ---\n' "$1"; }

section timestamp
date '+snapshot=%s|%Y-%m-%d|%H%M|%w-%m-%d|%z'

section active_release
for name in current-release previous-release; do
    printf '%s=' "$name"
    sed -n '1p' "/mnt/us/literary-clock/runtime/$name" 2>/dev/null || echo missing
done

section startup_framework
sed -n '1,240p' /etc/upstart/kmc.conf 2>/dev/null || echo kmc.conf=missing
find /var/local/kmc -maxdepth 3 -type f 2>/dev/null | sort | sed -n '1,240p'
find /mnt/us/extensions -maxdepth 3 -type f 2>/dev/null | sort | sed -n '1,240p'

section commands
for command_name in sha256sum fsync sync start stop restart; do
    printf '%s=' "$command_name"
    command -v "$command_name" 2>/dev/null || echo missing
done
fsync --help 2>&1 | sed -n '1,100p' || true

section service_state
for path in \
    /var/local/literary-clock/service/session.tsv \
    /var/local/literary-clock/service/status.txt \
    /var/local/literary-clock/state/state.tsv \
    /mnt/us/literary-clock/runtime/service.conf; do
    echo "[$path]"
    sed -n '1,160p' "$path" 2>/dev/null || echo missing
done
printf 'kron_pids='; pidof kron 2>/dev/null || echo none
printf 'awesome_pids='; pidof awesome 2>/dev/null || echo none
printf 'cvm_pids='; pidof cvm 2>/dev/null || echo none
printf 'power_state='; lipc-get-prop com.lab126.powerd state 2>/dev/null || echo unknown
printf 'preventScreenSaver='; lipc-get-prop com.lab126.powerd preventScreenSaver 2>/dev/null || echo unknown
for property in activeApp activeView activeContext; do
    printf 'appmgrd_%s=' "$property"
    lipc-get-prop com.lab126.appmgrd "$property" 2>/dev/null || echo unknown
done

section recent_logs
for path in \
    /var/local/literary-clock/service/literary-clock.log \
    /mnt/us/literary-clock/runtime/kron/kron.log; do
    echo "[$path]"
    tail -n 120 "$path" 2>/dev/null || echo missing
done

section boot_hook
for path in \
    /mnt/us/emergency.sh \
    /mnt/us/literary-clock/runtime/emergency.sh.used \
    /mnt/us/literary-clock/runtime/emergency.sh.disabled; do
    echo "[$path]"
    sed -n '1,160p' "$path" 2>/dev/null || echo missing
done

section complete
date -u '+completed_at_utc=%Y-%m-%dT%H:%M:%SZ'
sync
