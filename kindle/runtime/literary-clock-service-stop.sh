#!/bin/sh
# Idempotently stop the Literary Clock service and restore the stock UI.

set -u

runtime_root=${LITCLOCK_RUNTIME_ROOT:-/mnt/us/literary-clock/runtime}
service_root=${LITCLOCK_SERVICE_ROOT:-/var/local/literary-clock/service}
session=$service_root/session.tsv
status=$service_root/status.txt
kron=${LITCLOCK_KRON:-$runtime_root/tools/kron}
if ! test -x "$kron"; then kron=$runtime_root/bin/kron; fi

read_session() {
    awk -F '\t' -v key="$1" '$1 == key { print $2; exit }' "$session" 2>/dev/null
}

if test -x "$kron"; then
    "$kron" -dir "$runtime_root/kron" remove literary-clock-minute >/dev/null 2>&1 || true
    "$kron" -dir "$runtime_root/kron" remove literary-clock-service-stop >/dev/null 2>&1 || true
fi

# Resume current processes by name, never by a PID persisted across reboot.
for name in cvm awesome; do
    pids=$(pidof "$name" 2>/dev/null || true)
    if test -n "$pids"; then kill -CONT $pids 2>/dev/null || true; fi
done

# Resuming the framework does not repaint an e-ink panel by itself. Request
# the modern KPP Home view explicitly, with the legacy booklet URI as a
# compatibility fallback.
home_restore=failed
if lipc-set-prop com.lab126.appmgrd start \
    'app://com.lab126.KPPMainApp?view=KPP_HOME' >/dev/null 2>&1; then
    home_restore=kpp
elif lipc-set-prop com.lab126.appmgrd start \
    'app://com.lab126.booklet.home' >/dev/null 2>&1; then
    home_restore=legacy
fi

original_prevent=
if test -r "$session"; then
    original_prevent=$(read_session original_prevent)
fi
case "$original_prevent" in
    0 | 1) lipc-set-prop com.lab126.powerd preventScreenSaver "$original_prevent" >/dev/null 2>&1 || true ;;
esac

mkdir -p "$service_root"
{
    echo "stopped_epoch=$(date +%s)"
    echo "stock_ui_restored=yes"
    echo "home_restore=$home_restore"
    echo "prevent_restored=${original_prevent:-unchanged}"
} >> "$status"
rm -f "$session" "$session.new"
sync

# A scheduled stop runs as a KindleCron child. Stopping the daemon may
# terminate that child, so all durable cleanup must already be complete.
if test -x "$kron"; then
    "$kron" -dir "$runtime_root/kron" stop >/dev/null 2>&1 || true
fi
exit 0
