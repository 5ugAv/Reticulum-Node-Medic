#!/usr/bin/env python3
"""Drop an nRF52 board into its UF2 bootloader with the 1200-baud touch.

Usage: techo_touch.py /dev/ttyACMx

Opening the CDC port at 1200 baud and dropping DTR is the Arduino convention
the Adafruit bootloader honours — proven on the T-Echo Plus 2026-08-19, no
hands needed. The OSError mid-call is EXPECTED: it is the port vanishing as
the board drops into the bootloader, i.e. the touch WORKING. Exit 0 either
way; the caller watches the bus for the bootloader identity, which is the
only real evidence.
"""
import sys
import time

import serial

port = sys.argv[1]
try:
    s = serial.Serial(port, 1200)
    try:
        s.dtr = False
        time.sleep(0.4)
    except OSError:
        pass          # port vanished mid-call: the touch worked
    try:
        s.close()
    except Exception:
        pass
except (OSError, serial.SerialException) as e:
    print(f"touch: could not open {port}: {e}", file=sys.stderr)
    sys.exit(1)
print("touch sent")
