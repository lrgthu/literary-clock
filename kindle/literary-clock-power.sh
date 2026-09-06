#!/bin/sh

# Reversible static-QA control for the stock Kindle screensaver.
# This is not a Phase 4B deep-sleep or scheduling solution.

set -u

service=com.lab126.powerd
property=preventScreenSaver
state_dir=/mnt/us/literary-clock/state
state_file="$state_dir/prevent-screen-saver.original"

read_value() {
    lipc-get-prop "$service" "$property"
}

case "${1:-}" in
    on)
        current=$(read_value) || exit 1
        case "$current" in
            0 | 1) ;;
            *)
                echo "unexpected $property value: $current" >&2
                exit 1
                ;;
        esac
        mkdir -p "$state_dir"
        if [ ! -f "$state_file" ]; then
            printf '%s\n' "$current" > "$state_file"
        fi
        lipc-set-prop "$service" "$property" 1
        enabled=$(read_value) || exit 1
        [ "$enabled" = 1 ] || {
            echo "failed to enable $property" >&2
            exit 1
        }
        echo "$property=$enabled"
        ;;
    off)
        original=0
        if [ -f "$state_file" ]; then
            original=$(sed -n '1p' "$state_file")
        fi
        case "$original" in
            0 | 1) ;;
            *)
                echo "invalid saved $property value: $original" >&2
                exit 1
                ;;
        esac
        lipc-set-prop "$service" "$property" "$original"
        restored=$(read_value) || exit 1
        [ "$restored" = "$original" ] || {
            echo "failed to restore $property=$original" >&2
            exit 1
        }
        if [ -f "$state_file" ]; then
            rm "$state_file"
        fi
        echo "$property=$restored"
        ;;
    status)
        current=$(read_value) || exit 1
        echo "$property=$current"
        if [ -f "$state_file" ]; then
            echo "saved_original=$(sed -n '1p' "$state_file")"
        else
            echo 'saved_original=none'
        fi
        ;;
    *)
        echo "usage: $0 on|off|status" >&2
        exit 2
        ;;
esac
