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
# The guard lives in ui_busy_guard.sh (2026-09-22): the hardware checks
# below plus the UI's own BUSY marker — a Pi birth runs over SSH inside the
# UI process and showed nothing here, and one was killed by a shell-side
# stop. STOP_ONLY=1 stops without starting (for a registry prune) under the
# SAME guard: there is no other sanctioned way to stop the UI.
bash "$(dirname "$0")/ui_busy_guard.sh" || exit 3
pkill -f "[p]ython3 .*main.py" 2>/dev/null
sleep 3
pkill -9 -f "[p]ython3 .*main.py" 2>/dev/null
sleep 1
if [ "${STOP_ONLY:-0}" = "1" ]; then echo "UI stopped (STOP_ONLY)"; exit 0; fi
setsid nohup env WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 DISPLAY=:0 XDG_SESSION_TYPE=tty bash ~/reticulum-tool/scripts/start_ui.sh >> ~/ui.log 2>&1 </dev/null &
