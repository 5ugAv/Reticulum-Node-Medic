#!/usr/bin/env bash
# Node Medic — render the scoped sudoers for THIS machine: its backlight and
# its app user.
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
# The source also names the app user `nodemedic` (the original medic's
# account). A CLONE runs as `pi`, so the user is rendered too: the grant line,
# the account's Defaults line (timestamp_timeout=0: sudo never remembers a
# password for it), the setfacl rule (u:<user>:rw) and the usermod rule
# (dialout <user>) — the only four places a user name appears outside a
# comment. Paths that merely contain the word (/usr/local/lib/nodemedic/...,
# /run/nodemedic/...) are never touched. With the default user the source
# passes through unchanged, so the original medic's file and any re-run on it
# are exactly what they always were.
#
# usage: render_sudoers.sh <source> <output> [backlight-device] [user]
#   backlight-device defaults to the first entry of /sys/class/backlight on
#   this machine (an empty argument means the same). With none present (an
#   HDMI panel, a headless box) the backlight path is left as it is: tee to a
#   path that does not exist fails, which is exactly what the slider reports.
#   user defaults to nodemedic.
set -euo pipefail
SRC="${1:?source sudoers file}"
OUT="${2:?output path}"
DEV="${3:-}"
NM_USER="${4:-nodemedic}"
PINNED="panel_backlight@1"
DEFAULT_USER="nodemedic"

# A login name: lower case, digits, _ and -, not starting with a digit or -.
# Anything else is refused before it can reach a sed expression or a rule.
# (The letters are spelled out: a range like a-z follows the locale's
# collation in some shells and lets capitals through.)
case "$NM_USER" in
    ""|-*|[0123456789]*|*[!abcdefghijklmnopqrstuvwxyz0123456789_-]*)
        echo "render_sudoers: refusing an odd user name: $NM_USER" >&2; exit 1;;
esac
if [ "${#NM_USER}" -gt 32 ]; then
    echo "render_sudoers: refusing an over-long user name" >&2; exit 1
fi

if [ -z "$DEV" ]; then
    DEV="$(ls /sys/class/backlight 2>/dev/null | head -n 1 || true)"
fi
# a sysfs device name: letters and digits, plus the few marks the kernel uses
if [ -n "$DEV" ] && [ "$DEV" != "$PINNED" ] && \
        ! printf '%s' "$DEV" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_@.:-]*$'; then
    echo "render_sudoers: refusing an odd backlight device name: $DEV" >&2
    exit 1
fi

TMP_OUT="$(mktemp)"
trap 'rm -f "$TMP_OUT" "$TMP_OUT.user"' EXIT
cp "$SRC" "$TMP_OUT"

if [ -n "$DEV" ] && [ "$DEV" != "$PINNED" ]; then
    sed "s#/sys/class/backlight/${PINNED}/brightness#/sys/class/backlight/${DEV}/brightness#" \
        "$SRC" > "$TMP_OUT"
    echo "render_sudoers: brightness rule rendered for $DEV (source pins $PINNED)"
fi

if [ "$NM_USER" != "$DEFAULT_USER" ]; then
    # Four anchored substitutions, each in the one context a user name can
    # take in this file. Portable BRE (the tests run this on macOS too).
    sed -e "s#^${DEFAULT_USER} ALL=(root) NOPASSWD:#${NM_USER} ALL=(root) NOPASSWD:#" \
        -e "s#^Defaults:${DEFAULT_USER} #Defaults:${NM_USER} #" \
        -e "s#u\\\\:${DEFAULT_USER}\\\\:rw#u\\\\:${NM_USER}\\\\:rw#g" \
        -e "s#usermod -aG dialout ${DEFAULT_USER}\$#usermod -aG dialout ${NM_USER}#" \
        -e "s#usermod -aG dialout ${DEFAULT_USER}\\([^a-z0-9_-]\\)#usermod -aG dialout ${NM_USER}\\1#" \
        "$TMP_OUT" > "$TMP_OUT.user"
    mv "$TMP_OUT.user" "$TMP_OUT"
    # A half-rendered policy would grant the wrong account, or nothing: prove
    # the user moved everywhere it appears in a rule before handing it on.
    rules="$(grep -v '^[[:space:]]*#' "$TMP_OUT")"
    if [ "$(printf '%s\n' "$rules" | grep -c "^${NM_USER} ALL=(root) NOPASSWD:")" != 1 ] || \
            printf '%s\n' "$rules" | grep -q "^${DEFAULT_USER} ALL=" || \
            printf '%s\n' "$rules" | grep -q "^Defaults:${DEFAULT_USER}[^a-z0-9_-]" || \
            printf '%s\n' "$rules" | grep -q "u\\\\:${DEFAULT_USER}\\\\:" || \
            printf '%s\n' "$rules" | grep -Eq "dialout ${DEFAULT_USER}([^a-z0-9_-]|\$)"; then
        echo "render_sudoers: the user did not render cleanly — refusing" >&2
        exit 1
    fi
    echo "render_sudoers: rules rendered for the app user $NM_USER"
fi

# For every user, the default included: sudo must never remember a password
# for the app account (the medic's sudo shares one time stamp across all of an
# account's sessions). A policy without exactly this one line is refused.
if [ "$(grep -c "^Defaults:${NM_USER} timestamp_timeout=0\$" "$TMP_OUT")" != 1 ]; then
    echo "render_sudoers: no 'Defaults:${NM_USER} timestamp_timeout=0' line — refusing" >&2
    exit 1
fi

cp "$TMP_OUT" "$OUT"
