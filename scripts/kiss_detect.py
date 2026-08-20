#!/usr/bin/env python3
"""Ask an RNode-protocol board if it is LISTENING yet. Exit 0 on a real answer.

Usage: kiss_detect.py /dev/ttyACMx [timeout_seconds]

USB enumeration is not readiness: on nRF52 the KISS handler only runs once
setup() finishes, and a FIRST-ever boot formats LittleFS in setup — tens of
seconds during which the port exists and says nothing. rnodeconf connects,
waits ~3s, and declares "RNode did not respond" about a board that is merely
busy being born (RAK4631 maiden birth, 2026-08-20). This probe sends
CMD_DETECT (0x08 0x73) and succeeds only on the firmware's own DETECT_RESP
(0x46) — the smallest true statement of "the board is talking".
"""
import sys
import time

import serial

PORT = sys.argv[1]
TIMEOUT = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0

deadline = time.time() + TIMEOUT
while time.time() < deadline:
    try:
        s = serial.Serial(PORT, 115200, timeout=1)
    except (OSError, serial.SerialException):
        time.sleep(1.5)
        continue
    try:
        s.reset_input_buffer()
        s.write(bytes([0xC0, 0x08, 0x73, 0xC0]))
        s.flush()
        buf = b""
        end = time.time() + 2.5
        while time.time() < end:
            buf += s.read(64)
            if b"\xc0\x08\x46\xc0" in buf:
                print("detect: answered")
                sys.exit(0)
    finally:
        try:
            s.close()
        except Exception:
            pass
    time.sleep(1.5)
print("detect: no answer", file=sys.stderr)
sys.exit(1)
