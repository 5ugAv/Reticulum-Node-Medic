#!/bin/bash
# Exit 3 (and say why) if the Node Medic UI must not be stopped right now.
# Sourced by restart_ui.sh; usable alone before any shell-side stop.
#   * hardware in progress: an SD image write, a board flash / DFU / upload
#   * the UI's own BUSY marker, fresh within 90 s (a birth over SSH, a walk,
#     an image download, a self-check — anything the UI knows it is doing;
#     see monitor/busy_marker.py, 2026-09-22)
# FORCE=1 overrides — for when the running instance is the thing that is broken.
if [ "${FORCE:-0}" = "1" ]; then exit 0; fi
busy=""
pgrep -f "[x]zcat" >/dev/null 2>&1 && busy="an SD card image write"
pgrep -f "[d]d .*of=/dev/sd" >/dev/null 2>&1 && busy="an SD card write"
pgrep -f "[e]sptool" >/dev/null 2>&1 && busy="a board firmware flash"
pgrep -f "[r]nodeconf" >/dev/null 2>&1 && busy="an RNode provisioning run"
pgrep -f "[a]dafruit-nrfutil" >/dev/null 2>&1 && busy="an nRF52 serial DFU flash"
pgrep -f "[a]rduino-cli upload" >/dev/null 2>&1 && busy="a board firmware upload"
marker="${UI_BUSY_MARKER:-$HOME/.reticulum-node-medic/ui_busy}"
if [ -z "$busy" ] && [ -f "$marker" ]; then
    now=$(date +%s); mt=$(stat -c %Y "$marker" 2>/dev/null || stat -f %m "$marker" 2>/dev/null || echo 0)
    if [ $((now - mt)) -le 90 ]; then
        busy="$(head -1 "$marker" 2>/dev/null || echo busy) (the UI says so)"
    fi
fi
if [ -n "$busy" ]; then
    echo "REFUSING to stop the UI: $busy is in progress." >&2
    echo "Wait for it to finish, or re-run with FORCE=1 if you are certain." >&2
    exit 3
fi
exit 0
