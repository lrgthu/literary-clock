#!/bin/sh
# Display exactly one validated base frame plus its renderer-owned date overlay.
# This helper owns no selection/history state.

set -u

frame=${1:-}
date_frame=${2:-}
date_x=${3:-}
date_y=${4:-}
waveform=${5:-GL16}
fbink=${LITCLOCK_FBINK:-/mnt/us/libkh/bin/fbink}

usage() {
    echo "usage: $0 FRAME DATE_FRAME DATE_X DATE_Y [GL16|GC16]" >&2
    exit 2
}

png_ok() {
    test -r "$1" || return 1
    test -s "$1" || return 1
    signature=$(dd if="$1" bs=8 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
    test "$signature" = 89504e470d0a1a0a
}

test -n "$frame" && test -n "$date_frame" && test -n "$date_x" && test -n "$date_y" || usage
case "$waveform" in
    GL16 | GC16) ;;
    *) usage ;;
esac
case "$date_x:$date_y" in
    *[!0-9:]* | :* | *:) usage ;;
esac

png_ok "$frame" || {
    echo "invalid or missing base PNG: $frame" >&2
    exit 3
}
png_ok "$date_frame" || {
    echo "invalid or missing date PNG: $date_frame" >&2
    exit 4
}

if test -n "${LITCLOCK_FAKE_DISPLAY_STATUS:-}"; then
    exit "$LITCLOCK_FAKE_DISPLAY_STATUS"
fi

test -x "$fbink" || {
    echo "FBInk is not executable: $fbink" >&2
    exit 5
}

# Batch both raster writes into the framebuffer, then issue one physical update.
"$fbink" -b -c -D PASSTHROUGH -i "$frame" || exit 6
"$fbink" -b -D PASSTHROUGH -g "file=$date_frame,x=$date_x,y=$date_y" || exit 7
"$fbink" -w -s -W "$waveform" -D PASSTHROUGH || exit 8
