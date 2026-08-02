"""Is there a powered USB hub in the path to this board?

The medic refuses some Pi + radio pairings because the Pi cannot feed the radio
(a Pi Zero browns out a Heltec V4 on transmit). A powered hub removes that
problem entirely, so a refusal is wrong when one is present — the operator asked
for exactly this (2026-08-02).

TWO THINGS THIS CANNOT KNOW, both of which shape the answer:

1. **A hub on the bench is not a hub in the field.** The brown-out happens when
   the finished node runs on its own, with the radio hanging off the Pi. Seeing
   a hub on the MEDIC only proves the operator owns one and is using it now. So
   detection here is evidence for a question, never a silent override — the flow
   asks whether that hub travels with the node.

2. **Bus-powered hubs lie.** The self-powered bit is set by cheap hubs that have
   no barrel jack at all, which is a well-known annoyance. A false positive here
   would un-refuse a pairing that really does brown out, so nothing is decided
   on this alone.

Reads sysfs only; no root, no writes, nothing that disturbs a running build.
"""

from __future__ import annotations

import os
from typing import List, Optional

SYS_USB = "/sys/bus/usb/devices"

#: USB class 09 = hub.
_HUB_CLASS = "09"

#: bmAttributes bit 6 — set means the device is self-powered rather than drawing
#: everything from upstream. Root hubs report e0 (verified on the medic,
#: 2026-08-02); a mains-powered external hub reports it too.
_SELF_POWERED = 0x40


def _read(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except Exception:
        return ""


def is_self_powered(bm_attributes: str) -> Optional[bool]:
    """True/False from a bmAttributes hex string, or None if unreadable."""
    if not bm_attributes:
        return None
    try:
        return bool(int(bm_attributes, 16) & _SELF_POWERED)
    except ValueError:
        return None


def _is_root_hub(name: str) -> bool:
    """usb1, usb2 … are the Pi's own controllers, not something plugged in."""
    return name.startswith("usb") and name[3:].isdigit()


def external_hubs(sys_usb: str = SYS_USB) -> List[dict]:
    """Every non-root USB hub the medic can see.

    ``{name, product, self_powered, max_power}``. Empty when nothing is plugged
    in — which on this medic is the normal state.
    """
    out = []
    try:
        names = sorted(os.listdir(sys_usb))
    except Exception:
        return out
    for name in names:
        d = os.path.join(sys_usb, name)
        if _read(os.path.join(d, "bDeviceClass")) != _HUB_CLASS:
            continue
        if _is_root_hub(name):
            continue                     # the Pi's own controller
        out.append({
            "name": name,
            "product": _read(os.path.join(d, "product")) or "USB hub",
            "self_powered": is_self_powered(
                _read(os.path.join(d, "bmAttributes"))),
            "max_power": _read(os.path.join(d, "bMaxPower")),
        })
    return out


def powered_hub_present(sys_usb: str = SYS_USB) -> Optional[dict]:
    """The first external hub claiming to be self-powered, or None.

    None means "no evidence", NOT "no hub" — an unreadable sysfs and an empty
    bench give the same answer, and the caller must treat both as "keep the
    warning" rather than as permission.
    """
    for hub in external_hubs(sys_usb):
        if hub.get("self_powered"):
            return hub
    return None


def hub_question(hub: dict, pi_name: str, board_name: str) -> dict:
    """What to put on screen once a powered hub is seen. Pure copy.

    Phrased as a question because of limit (1) above: the hub has to go WITH the
    node. A hub that stays on the bench changes nothing about the node that
    ships, and the operator is the only one who knows which it is.
    """
    return {
        "head": f"A powered hub is connected ({hub.get('product', 'USB hub')})",
        "body": (f"If {board_name} runs through that hub on the finished node, "
                 f"{pi_name} never has to power it and this pairing is fine."),
        "confirm": "Yes — the hub stays with the node",
        "note": ("The hub has to travel with the node. If it stays on the "
                 "bench, the node will still brown out when it transmits."),
    }
