"""Translate a tty into the number engraved beside its USB hole.

The medic's case has its four USB-A holes engraved 1-4, and "/dev/ttyACM1"
means nothing to an operator crouched over the box in the field — "Port 3"
does. The kernel names a device by its <bus>-<port> path segment (e.g.
/sys/bus/usb/devices/1-2), and which segment belongs to which engraved hole
is a fact about THIS medic's wiring, not something software can derive: we
measured it on 2026-08-21 by walking one board through every hole and
watching where it appeared. Jonesey (the medic's own permanent radio, in the
hole beside Ethernet) anchors the map — it must always read Port 1.

Honesty rule inherited from SPEC.md: a port number is either measured or
absent. On any other machine, on a bus we haven't measured, or when udevadm
won't answer, every function here returns None / the raw device path — never
a guess.
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

#: Physical engraved hole per kernel <bus>-<port> segment, measured live on
#: 2026-08-21 by moving a single board through all four holes on THIS Pi 5.
#: Port 1 (top-left, beside Ethernet) is Jonesey's permanent home.
#:
#: CAVEAT — shadow buses: a USB-3 device in one of the two blue holes shows
#: up on bus 2 or 4 ("2-1", "4-2"), and we have NOT walked a USB-3 device
#: through the holes to learn which blue hole is which. Those segments are
#: deliberately absent, so a 2-x/4-x device falls back to its raw /dev path
#: rather than getting a number we never measured.
MEDIC_PORT_MAP = {
    "3-2": 1,   # top-left, beside Ethernet — Jonesey
    "1-1": 2,   # top-right
    "1-2": 3,   # bottom-left
    "3-1": 4,   # bottom-right
}

#: A standalone <bus>-<port> path component, hub chains included ("1-1.3").
#: The ":1.0"-style interface suffix makes a component NOT match, which is
#: what keeps ".../1-2/1-2:1.0/..." resolving to "1-2".
_SEGMENT = re.compile(r"^\d+-\d+(\.\d+)*$")


def usb_path_for_tty(dev: str) -> Optional[str]:
    """The kernel <bus>-<port> segment behind *dev* ("1-2", "1-1.3"), or None.

    Asks udevadm for the sysfs path and takes the LAST plain segment before
    the interface suffix — the deepest hop is the device itself; earlier ones
    are hubs on the way. Any failure (no udevadm on this machine, unknown
    device, timeout) is None, never a guess."""
    if not dev:
        return None
    try:
        out = subprocess.run(
            ["udevadm", "info", "-q", "path", "-n", dev],
            capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    segments = [p for p in (out.stdout or "").strip().split("/")
                if _SEGMENT.match(p)]
    return segments[-1] if segments else None


def case_port_label(dev: str) -> Optional[str]:
    """"Port 3", "Port 2 (via hub)", or None when we don't KNOW.

    A dotted segment ("1-1.3") means the board hangs off a hub plugged into
    the hole — the root before the first dot is the hole, and the label says
    so. Shadow-bus (2-x/4-x) and off-medic segments aren't in the map, so
    they come back None and the caller shows the raw path instead."""
    segment = usb_path_for_tty(dev)
    if not segment:
        return None
    root, dot, _ = segment.partition(".")
    number = MEDIC_PORT_MAP.get(root)
    if number is None:
        return None
    return f"Port {number} (via hub)" if dot else f"Port {number}"


def describe_port(dev: str) -> str:
    """The drop-in for user-facing strings: "Port 3 (/dev/ttyACM1)" when the
    hole is known, otherwise just the raw device path unchanged."""
    label = case_port_label(dev)
    return f"{label} ({dev})" if label else (dev or "")
