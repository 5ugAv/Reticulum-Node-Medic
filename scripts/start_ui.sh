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
exec /usr/bin/python3 main.py
