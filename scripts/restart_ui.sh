#!/bin/bash
# Restart the Node Medic UI. Self-safe pkill (bracket trick + matches -u flag);
# escalates to -9 for an instance wedged in a UI hot-loop (SIGTERM ignored).
#
# REFUSES while the medic is writing hardware. On 2026-08-02 a deploy restarted
# the UI 80 seconds into an SD card write: the card was left half-written with
# the stock config, and the operator came back to a home screen with no idea
# why. A restart is never so urgent that it is worth destroying work in
# progress, and the person typing it usually cannot see the screen.
# Override with FORCE=1 when the running instance is the thing that is broken.
if [ "${FORCE:-0}" != "1" ]; then
    busy=""
    # An SD image write: xzcat feeding dd, or dd straight onto a USB disk.
    pgrep -f "[x]zcat" >/dev/null 2>&1 && busy="an SD card image write"
    pgrep -f "[d]d .*of=/dev/sd" >/dev/null 2>&1 && busy="an SD card write"
    # A board flash. Interrupting one mid-erase can leave a board that will not
    # re-enter its bootloader without hands-on recovery.
    pgrep -f "[e]sptool" >/dev/null 2>&1 && busy="a board firmware flash"
    pgrep -f "[r]nodeconf" >/dev/null 2>&1 && busy="an RNode provisioning run"
    # nRF52 boards flash over serial DFU, not esptool. Interrupting one is
    # worse than interrupting an ESP32: a board like the Heltec MeshPocket
    # cannot be power-cycled at all, so recovery is a button press the
    # operator may not be near.
    pgrep -f "[a]dafruit-nrfutil" >/dev/null 2>&1 && busy="an nRF52 serial DFU flash"
    pgrep -f "[a]rduino-cli upload" >/dev/null 2>&1 && busy="a board firmware upload"
    if [ -n "$busy" ]; then
        echo "REFUSING to restart: $busy is in progress." >&2
        echo "Wait for it to finish, or re-run with FORCE=1 if you are certain." >&2
        exit 3
    fi
fi
pkill -f "[p]ython3 .*main.py" 2>/dev/null
sleep 3
pkill -9 -f "[p]ython3 .*main.py" 2>/dev/null
sleep 1
setsid nohup env WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0 XDG_SESSION_TYPE=tty bash ~/reticulum-tool/scripts/start_ui.sh >> ~/ui.log 2>&1 </dev/null &
