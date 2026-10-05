#!/usr/bin/env bash
# Node Medic — render the scoped sudoers for THIS machine's backlight.
#
# provisioning/sudoers.d/nodemedic pins the brightness write to ONE sysfs path:
#   /usr/bin/tee /sys/class/backlight/panel_backlight@1/brightness
# panel_backlight@1 is the developer's 5-inch DSI panel. On a medic built with
# another display the device is called something else, so the Settings slider
# moved, showed a percentage and did nothing: the app globs
# /sys/class/backlight/* and finds the right device, but sudo refused the path
# it then asked for (readiness ledger #16). The policy is rendered for the
# machine it is installed on, here, before visudo validates it.
#
# usage: render_sudoers.sh <source> <output> [backlight-device]
#   backlight-device defaults to the first entry of /sys/class/backlight on
#   this machine. With none present (an HDMI panel, a headless box) the source
#   is copied unchanged: tee to a path that does not exist fails, which is
#   exactly what the slider then reports. Only that one path is substituted.
set -euo pipefail
SRC="${1:?source sudoers file}"
OUT="${2:?output path}"
DEV="${3:-}"
PINNED="panel_backlight@1"
if [ -z "$DEV" ]; then
    DEV="$(ls /sys/class/backlight 2>/dev/null | head -n 1 || true)"
fi
if [ -z "$DEV" ] || [ "$DEV" = "$PINNED" ]; then
    cp "$SRC" "$OUT"
    exit 0
fi
# a sysfs device name: letters and digits, plus the few marks the kernel uses
if ! printf '%s' "$DEV" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_@.:-]*$'; then
    echo "render_sudoers: refusing an odd backlight device name: $DEV" >&2
    exit 1
fi
sed "s#/sys/class/backlight/${PINNED}/brightness#/sys/class/backlight/${DEV}/brightness#" \
    "$SRC" > "$OUT"
echo "render_sudoers: brightness rule rendered for $DEV (source pins $PINNED)"
