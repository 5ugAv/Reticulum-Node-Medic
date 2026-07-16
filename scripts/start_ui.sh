#!/bin/bash
# Launch the Node Medic UI on the touchscreen (used by the desktop autostart).
# Keeps the display awake — this is a field instrument, not a desktop.
xset s off -dpms s noblank 2>/dev/null
cd /home/nodemedic/reticulum-tool || exit 1
exec /usr/bin/python3 main.py
