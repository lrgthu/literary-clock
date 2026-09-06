#!/bin/sh
# Name: Literary Clock Phase 4B Read-Only Audit
# Author: Literary Clock
# DontUseFBInk

# Read-only capability inventory. The only write is this report on user storage.

set -u

base=/mnt/us/literary-clock
report="$base/phase4b-device-audit.txt"
fbink=/mnt/us/libkh/bin/fbink

mkdir -p "$base"
exec > "$report" 2>&1

section() {
    printf '\n--- %s ---\n' "$1"
}

section identity
id
uname -a
printf 'shell=%s\n' "${SHELL:-unknown}"
printf 'pid1='; ps | sed -n '2p'
for version_file in /etc/prettyversion.txt /etc/version.txt /mnt/us/system/version.txt; do
    if [ -r "$version_file" ]; then
        printf '%s=' "$version_file"
        sed -n '1p' "$version_file"
    fi
done

section atomic_local_timestamp
date '+snapshot=%s|%Y-%m-%d|%H%M|%a, %b %-d %H:%M:%S %Z' 2>/dev/null || \
    date '+snapshot=%s|%Y-%m-%d|%H%M|%a, %b %d %H:%M:%S %Z'
date -u '+utc=%s|%Y-%m-%d|%H%M|%Y-%m-%dT%H:%M:%SZ'

section storage
df -k /mnt/us /var/local /tmp 2>/dev/null || true
mount | sed -n '1,160p'

section commands
for command_name in \
    sh ash busybox awk sed grep cut tr sort uniq head tail wc find xargs cksum md5sum \
    sha1sum sha256sum stat sync mv cp date sleep usleep flock logger killall pidof lipc-probe \
    lipc-get-prop lipc-set-prop eips; do
    command_path=$(command -v "$command_name" 2>/dev/null || true)
    printf '%s=%s\n' "$command_name" "${command_path:-missing}"
done
if command -v busybox >/dev/null 2>&1; then
    printf 'busybox_version='; busybox 2>&1 | sed -n '1p'
fi

section fbink
if [ -x "$fbink" ]; then
    ls -l "$fbink"
    "$fbink" -h 2>&1 | sed -n '1,12p'
else
    echo 'fbink=missing'
fi

section powerd
lipc-probe -v com.lab126.powerd 2>/dev/null || true
for property in \
    state status preventScreenSaver disableScreenOff screenSaverTimeout battLevel isCharging \
    flIntensity; do
    printf '%s=' "$property"
    lipc-get-prop com.lab126.powerd "$property" 2>/dev/null || echo unavailable
done

section rtc
if [ -r /proc/driver/rtc ]; then
    cat /proc/driver/rtc
fi
for rtc_path in /sys/class/rtc/rtc0/name /sys/class/rtc/rtc0/since_epoch \
    /sys/class/rtc/rtc0/wakealarm /sys/class/rtc/rtc0/date /sys/class/rtc/rtc0/time; do
    if [ -r "$rtc_path" ]; then
        printf '%s=' "$rtc_path"
        cat "$rtc_path"
    fi
done
ls -la /sys/class/rtc/rtc0 2>/dev/null || true

section scheduler
printf 'crond_pids='; pidof crond 2>/dev/null || echo none
printf 'cron_pids='; pidof cron 2>/dev/null || echo none
for cron_path in /etc/crontab /etc/cron.d /etc/cron.daily /etc/cron.hourly \
    /var/spool/cron /var/spool/cron/crontabs; do
    if [ -e "$cron_path" ]; then
        ls -lad "$cron_path"
        if [ -f "$cron_path" ]; then
            sed -n '1,160p' "$cron_path"
        else
            find "$cron_path" -maxdepth 2 -type f -print 2>/dev/null | sed -n '1,160p'
        fi
    fi
done
find /mnt/us /var/local -maxdepth 5 \( -iname '*cron*' -o -iname '*wake*' \) \
    -print 2>/dev/null | sed -n '1,240p'

section jailbreak_and_startup
for path in /var/local/kmc /var/local/kmc/bin /mnt/us/libkh /mnt/us/extensions \
    /etc/upstart /etc/init.d; do
    if [ -e "$path" ]; then
        ls -lad "$path"
    fi
done
find /var/local/kmc -maxdepth 4 -type f -print 2>/dev/null | sed -n '1,240p'
find /etc/upstart -maxdepth 2 -type f -print 2>/dev/null | sed -n '1,240p'

section processes
ps w 2>/dev/null || ps

section framework
for process_name in awesome cvm blanket powerd; do
    printf '%s=' "$process_name"
    pidof "$process_name" 2>/dev/null || echo none
done
for service_property in orientation orientationLock chromeState isScreenSaverLayerWindowActive; do
    printf '%s=' "$service_property"
    lipc-get-prop com.lab126.winmgr "$service_property" 2>/dev/null || echo unavailable
done

section update_blocking
find /mnt/us -maxdepth 1 -type f -name '*.bin' -print
find /mnt/us -maxdepth 3 \( -iname '*update*block*' -o -iname '*ota*block*' \) \
    -print 2>/dev/null | sed -n '1,160p'

section complete
date -u '+completed_at_utc=%Y-%m-%dT%H:%M:%SZ'
sync
