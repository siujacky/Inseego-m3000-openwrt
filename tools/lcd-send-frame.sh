#!/bin/sh
# Send a complete RGB888 frame through the actual LCD character device.
set -eu
init=0
if [ "${1:-}" = --init ]; then init=1; shift; fi
[ "$#" -eq 1 ] || { echo 'usage: lcd-send-frame.sh [--init] frame.rgb888' >&2; exit 2; }
frame=$1
[ -f "$frame" ] && [ "$(wc -c < "$frame")" -eq 230400 ] || {
    echo 'frame must contain exactly 230400 RGB888 bytes' >&2; exit 2;
}
device=/dev/m3100-lcd
for attempt in 1 2 3 4 5; do
    [ -c "$device" ] && break
    sleep 1
done
[ -c "$device" ] || { echo 'LCD character device unavailable; refusing regular-file write' >&2; exit 1; }
if [ "$init" -eq 1 ]; then echo bars > "$device"; fi
{ echo frame; cat "$frame"; } > "$device"
echo 'LCD frame transferred'
