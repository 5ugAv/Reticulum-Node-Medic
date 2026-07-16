#!/bin/bash
# Launch the Node Medic UI on the touchscreen (used by the desktop autostart).
# Keeps the display awake — this is a field instrument, not a desktop.
xset s off -dpms s noblank 2>/dev/null
cd /home/nodemedic/reticulum-tool || exit 1
# The 5" panel is ~295 DPI; Kivy assumes desktop DPI, rendering text half-size.
# Density scales every sp (text) and dp (touch target) together, app-wide.
export KIVY_METRICS_DENSITY=1.5
exec /usr/bin/python3 main.py
