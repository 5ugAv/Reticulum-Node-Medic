#!/usr/bin/env python3
"""Reset an nRF52 RTNode over KISS and capture its boot log.

Usage: techo_bootlog.py /dev/ttyACMx [seconds]

The ESP32 RTNodes are reset with an RTS pulse (serial_capture_cmd) — that
wiring does not exist on native-USB nRF52, so the reset is the firmware's own
KISS CMD_RESET (0x55 0xF8). The board re-enumerates (its ttyACM number can
move), so the port is re-resolved by its by-id identity and reopened FAST:
setup() waits up to ~4s for a host, which is what makes the earliest prints
catchable at all (proven on the bench, 2026-08-19).

Output: the captured text on stdout, KISS frames stripped to keep the log
readable — the caller parses lines like "[RTNode] identity=… dst=…".
"""
import glob
import os
import sys
import time

import serial

PORT = sys.argv[1]
SECONDS = int(sys.argv[2]) if len(sys.argv) > 2 else 40


def find_port():
    links = (glob.glob("/dev/serial/by-id/*T-Echo*")
             + glob.glob("/dev/serial/by-id/*RTNode*")
             + glob.glob("/dev/serial/by-id/*Nordic*"))
    for l in links:
        p = os.path.realpath(l)
        if os.path.exists(p):
            return p
    return None


try:
    s = serial.Serial(PORT, 115200, timeout=1)
    s.write(bytes([0xC0, 0x55, 0xF8, 0xC0]))    # KISS CMD_RESET
    s.flush()
    s.close()
except (OSError, serial.SerialException):
    pass                                        # port may die mid-write: fine

time.sleep(0.5)
deadline = time.time() + 30
sess = None
while time.time() < deadline:
    p = find_port()
    if p:
        try:
            sess = serial.Serial(p, 115200, timeout=1)
            break
        except (OSError, serial.SerialException):
            pass
    time.sleep(0.2)

if sess is None:
    print("BOOTLOG: board did not re-enumerate", file=sys.stderr)
    sys.exit(1)

buf = b""
end = time.time() + SECONDS
while time.time() < end:
    buf += sess.read(4096)
sess.close()

# strip KISS frames (0xC0 ... 0xC0) so the text log stays parseable
out, in_frame = [], False
for b in buf:
    if b == 0xC0:
        in_frame = not in_frame
        continue
    if not in_frame and (32 <= b < 127 or b in (10, 13)):
        out.append(chr(b))
print("".join(out))
