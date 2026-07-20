"""The medic's own permanent hardware, identified by USB serial — so the tool
never confuses *its own* radio/GPS with a work board it's servicing.

Node Medic carries permanent infrastructure: its own LoRa RNode (Jonesey, the
mesh vantage) and its GPS board (a Heltec Wireless Tracker). Everything ELSE on
USB is a work board to flash / PROBE / birth. Telling them apart by "is the port
busy?" is fragile: stop rnsd for maintenance and Jonesey's port goes free, so a
busy-check would suddenly see the medic's own radio as flashable — a foot-gun
that could erase the medic's radio. And a Heltec Tracker plugged in *to be
serviced* looks identical to the medic's *own* GPS Tracker under a busy-check.

So the medic records its own boards by **USB serial** (a stable identity) in a
small roster file, and target-selection excludes them by identity. A cloned medic
registers ITS own boards at setup — self-knowledge that travels with the fleet.
"""

from __future__ import annotations

import glob
import json
import os
import re

#: Per-medic roster: {role: usb_serial}, e.g. {"jonesey_lora": "3C:0F:02:EB:2E:18"}.
ROSTER_PATH = os.path.expanduser("~/.reticulum-node-medic/onboard.json")

_SERIAL_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def serial_for_port(port: str) -> str | None:
    """The USB serial (an ESP32 MAC, e.g. ``3C:0F:02:EB:2E:18``) for a
    ``/dev/ttyACM*`` / ``/dev/ttyUSB*`` port, resolved from the stable
    ``/dev/serial/by-id`` symlink. None if it can't be resolved."""
    target = os.path.realpath(port)
    for link in glob.glob("/dev/serial/by-id/*"):
        try:
            if os.path.realpath(link) == target:
                base = os.path.basename(link)
                m = _SERIAL_RE.search(base)
                return m.group(1) if m else base
        except OSError:
            continue
    return None


def load_roster(path: str = ROSTER_PATH) -> dict:
    """The medic's onboard roster ({role: serial}); empty if none recorded yet."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def onboard_serials(path: str = ROSTER_PATH) -> set:
    """The USB serials of the medic's OWN permanent boards (never work targets)."""
    return {v for v in load_roster(path).values() if v}


def is_onboard(port: str, path: str = ROSTER_PATH) -> bool:
    """True when *port* is one of the medic's own permanent boards (by identity),
    so flash / PROBE / birth must never target it — even if rnsd is stopped and
    the port looks free."""
    serial = serial_for_port(port)
    return bool(serial and serial in onboard_serials(path))


def register(role: str, serial: str, path: str = ROSTER_PATH) -> dict:
    """Record one of the medic's own permanent boards. Idempotent; returns the
    updated roster. (A cloned medic calls this for each of its boards at setup.)"""
    roster = load_roster(path)
    roster[role] = serial
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(roster, f, indent=2, sort_keys=True)
    return roster


def register_port(role: str, port: str, path: str = ROSTER_PATH) -> dict | None:
    """Register the board currently on *port* by resolving its USB serial. None
    if the serial can't be resolved (nothing recorded)."""
    serial = serial_for_port(port)
    if not serial:
        return None
    return register(role, serial, path)
