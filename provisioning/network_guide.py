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

#: WHAT TO BUILD, AND WHAT IT COSTS (operator, 2026-08-31: "this is probably
#: a page that we need inside Node Medic as recommended hardware for builds"
#: — then, seeing a second button appear beside the existing help icon:
#: "there is already the question mark icon in the corner of Birth. maybe
#: adding that information there is useful"). So it lives HERE, in the one
#: guide the "?" already opens, rather than competing with it.
#: Full reasoning: docs/WHICH_NODE_TO_BUILD.md, docs/CHEAPEST_NODE.md
BUILD_TITLE = "What should I build?"
BUILD_SECTIONS: List[Tuple[str, str, List[str]]] = [
    ("1. The everyday node — as many as you can make",
     "About AU$7 each (AU$5 in tens)",
     ["Classic ESP32 devkit + RFM95/SX1276 radio module.",
      "Most of a network should be made of these — they route and relay "
      "as well as a $60 board. What you give up is battery life, a screen, "
      "a case and GPS; not networking.",
      "The antenna is FREE: 8.2 cm of wire is a quarter-wave at 915 MHz. "
      "On our own bench an 8 cm stub beat a 40 cm whip and a $40 branded "
      "antenna — 8 cm simply IS the right length for this band.",
      "Power: mains, a USB power bank, or solar with a real panel — the "
      "ESP32 is not a low-power part."]),
    ("2. The remote node — put it somewhere and leave it",
     "AU$30–60",
     ["An nRF52840 + SX1262 board ON ITS OWN, as a transport node: "
      "RAK4631, Heltec T114, Heltec Mesh Solar, Seeed SenseCAP Solar Node.",
      "The radio board IS the node — it needs no computer attached. It "
      "sleeps at microamps and wakes for packets; listening costs about "
      "5–10 mA. Weeks on one 18650, indefinitely with a 2 W panel.",
      "DO NOT bolt a Raspberry Pi to one of these to save battery. A Pi "
      "draws 10–20× what the radio does — that pairing is the dearest "
      "build AND the shortest-lived.",
      "The purpose-built solar ones arrive with panel, battery and "
      "weatherproofing already solved."]),
    ("3. The message-holder — for people who are offline",
     "AU$60+ on mains; AU$120–200 done properly on solar",
     ["A Raspberry Pi + any supported radio.",
      "ONLY this kind of node can hold messages for someone whose device "
      "is switched off. A transport node cannot.",
      "A network wants a few of these, not many — one in a hall, a shop, "
      "a home with power.",
      "ON SOLAR a Pi needs REAL hardware: a Pi Zero 2 W (about 15 Wh a "
      "day), a 20 W panel minimum, about 45 Wh of battery for three "
      "cloudy days, and a proper charge controller. A 5 W panel matches a "
      "perfect day and dies on the first cloudy one.",
      "If mains power is anywhere nearby, use it — the money is better "
      "spent on more everyday nodes."]),
]
BUILD_ALWAYS: List[str] = [
    "Right band: 915 MHz for Australia, NZ and the US; 868 for Europe. A "
    "433 MHz module looks identical and will never join your network.",
    "Fit an antenna BEFORE powering it — transmitting with none damages "
    "the radio.",
    "3.3 V to the radio. Never 5 V.",
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
