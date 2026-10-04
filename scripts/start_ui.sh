#!/bin/bash
# Launch the Node Medic UI on the touchscreen (used by the desktop autostart).
# Keeps the display awake — this is a field instrument, not a desktop.
xset s off -dpms s noblank 2>/dev/null
cd /home/nodemedic/reticulum-tool || exit 1
# pip --user tools (pio for RTNode builds, rnodeconf/rnid for RNode flashes) live
# in ~/.local/bin — the autostart shell doesn't include it, so the flash steps
# hit "pio: command not found". Put it on PATH for the app + its subprocesses.
export PATH="$HOME/.local/bin:$PATH"
# The 5" panel is ~295 DPI; Kivy assumes desktop DPI, rendering text half-size.
# Density scales every sp (text) and dp (touch target) together, app-wide.
export KIVY_METRICS_DENSITY=1.5
# A finger is a finger, not also a mouse. ui/touch_input.py picks ONE provider
# for the panel — mtdev on the Goodix controller, with Kivy's mouse provider
# off (2026-10-03). These two SDL switches only matter on the fallback road
# (no libmtdev → sdl2 touch), where SDL would otherwise synthesise a mouse
# pointer from the first finger and every tap would land twice.
export SDL_TOUCH_MOUSE_EVENTS=0
export SDL_MOUSE_TOUCH_EVENTS=0
# Wait for rnsd's shared Reticulum instance BEFORE launching, so the app always
# attaches as a CLIENT. If the app starts first (boot race), RNS silently makes
# it the shared-instance SERVER — rnsd is orphaned and VITALS goes deaf to
# health beacons (the 2026-07-30 inversion incident). Budget 2 min, then launch
# anyway so a medic whose rnsd is genuinely broken still gets a UI (the in-app
# attach retry + Self Diagnose can take it from there).
for _i in $(seq 1 60); do
    ss -xl 2>/dev/null | grep -q "@rns/default" && break
    sleep 2
done
# -u: unbuffered stdout/stderr. ui.log is a pipe/file, so buffered prints (RNS
# log lines included) can sit invisible in an 8 KB buffer for hours — which hid
# the 2026-07-30 deaf-listener evidence. Live logs are worth the tiny cost.
# ONE LOG FOR EVERY ROAD (2026-09-23). Started by the desktop's autostart
# at boot, the UI's output went to ~/.xsession-errors; started by
# scripts/restart_ui.sh, to ~/ui.log — so a boot-time UI left nothing in
# the file every diagnosis reads, and two UIs launched by a doubled
# autostart went unnoticed for a whole boot. Append here, whoever calls.
exec /usr/bin/python3 -u main.py >> "$HOME/ui.log" 2>&1
