"""The Reticulum / LoRa quick-guide content — plain-language notes an operator
reads while deciding what to build and how to place it.

Pure data + light formatting helpers, NO Kivy, so it's unit-testable and can be
rendered as a full Settings screen OR as an inline "?" help popup during setup.
The canonical radio parameters are pulled live from provisioning.radio_defaults
so this guide and BIRTH never drift apart.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from provisioning import radio_defaults

TITLE = "Reticulum Nodes — the quick guide"

#: (term, one-line plain explanation). The three building blocks of the mesh.
CONCEPTS: List[Tuple[str, str]] = [
    ("RNode",
     "The radio hardware. Every device needs one to talk over LoRa. It's just "
     "the mouth and ears — it doesn't decide anything."),
    ("Transport node",
     "Connects messages live, like a phone call. If the person on the other end "
     "isn't there to pick up, the message doesn't get through."),
    ("Propagation node",
     "The answering machine of the network. If someone is offline, it holds their "
     "messages and delivers them when they come back. Without one, a message to an "
     "offline device simply vanishes."),
]

GOLDEN_RULE_TITLE = "The golden rule"
GOLDEN_RULE_BODY: List[str] = [
    "The network builds maps: \"to reach that node, go through the node on the "
    "water tower.\" When a node moves, every map that mentions it breaks — and the "
    "network wastes precious radio time rebuilding them.",
    "If it moves, it's a passenger. If it's bolted down and always on, it can be "
    "part of the road.",
    "Anything that moves — phone, car, backpack — should be an RNode peer only. "
    "Transport OFF.",
]

#: (device, role) placement cheat-sheet.
ROLES: List[Tuple[str, str]] = [
    ("Phone + RNode",
     "Peer. Transport OFF."),
    ("Car + RNode",
     "Peer with a great antenna. Transport OFF."),
    ("Rooftop node — radio board on its own (e.g. RTNode-2400), powered, never "
     "moves",
     "Transport node. The backbone."),
    ("Radio board attached to a Pi or other computer with storage, always on",
     "Propagation node. The answering machine."),
]

RADIO_TITLE = "Radio parameters (every node, same mesh)"
#: Human labels + units for the five modem params, in the order operators type them.
RADIO_ROWS: List[Tuple[str, str]] = [
    ("Frequency", "MHz"),
    ("Bandwidth", "kHz"),
    ("Spreading factor (SF)", ""),
    ("Coding rate (CR)", ""),
    ("TX power", "dBm"),
]


def radio_params() -> Dict[str, float]:
    """The canonical default modem params (freq, bw, sf, cr, txp) — the single
    source of truth shared with BIRTH's pre-fill."""
    return dict(radio_defaults.DEFAULT_PARAMS)


def radio_lines() -> List[str]:
    """The radio params as ready-to-read lines, e.g. 'Frequency — 915.125 MHz'."""
    p = radio_params()
    vals = {
        "Frequency": f"{p['freq']:g}",
        "Bandwidth": f"{p['bw']:g}",
        "Spreading factor (SF)": f"{int(p['sf'])}",
        "Coding rate (CR)": f"{int(p['cr'])}",
        "TX power": f"{int(p['txp'])}",
    }
    out = []
    for label, unit in RADIO_ROWS:
        v = vals[label]
        out.append(f"{label} — {v}{(' ' + unit) if unit else ''}".rstrip())
    return out
