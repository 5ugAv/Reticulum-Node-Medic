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
    # "Like a phone call" made it sound like the handset (operator, on the
    # glass 2026-09-24): a newcomer could expect to talk THROUGH a transport
    # node. It is the tower, not the phone — you still need an RNode of your
    # own to speak.
    ("Transport node",
     "The relay tower of the network. It passes messages along, live, between "
     "other devices — you don't talk through it yourself; for that you need "
     "your own RNode. If the person on the other end isn't there to pick up, "
     "the message doesn't get through."),
    ("Propagation node",
     "The answering machine of the network. If someone is offline, it holds their "
     "messages and delivers them when they come back. Without one, a message to an "
     "offline device simply vanishes."),
    # Two words of the medic's own that reached every screen with no
    # explanation anywhere (readiness ledger #213).
    ("Kin",
     "The nodes this medic built or adopted — the ones it watches on VITALS and "
     "can repair. A node you can hear but did not build stays a stranger until "
     "you adopt it."),
    ("The Tracker",
     "This medic's own Heltec Wireless Tracker: its GPS (the clock can be set "
     "from it by hand), flashed and "
     "adopted by Node Medic."),
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

#: HOW FAR DOES IT ACTUALLY REACH (operator, 2026-09-21, standing in the
#: yard with a T114 outdoors: the boundary walk had no entry behind the
#: red "?" — the one place a person already goes to ask what something is).
#: It sits here, after the roles table, because that table is where the
#: question arises: you have just been told which box is the backbone, and the
#: next thing you want to know is whether it can hear the next one.
#: i18n (2026-10-05, readiness #205): like every other string in this module
#: it stays English HERE and is translated at render time by
#: ui.widgets.guide_content (tr() on each title, paragraph and line), so the
#: catalogs under assets/i18n key on these exact sentences. Keep the two in
#: step: a reworded sentence here is a new catalog key.
REACH_TITLE = "How far does a node actually reach? — the boundary walk"
REACH_BODY: List[str] = [
    "Coverage maps and datasheet ranges are guesses. The only honest answer "
    "is the one you measure, and a boundary walk is how Node Medic measures "
    "it: leave the node where it will live, then walk away from it carrying "
    "the medic. It pings the node every 20 seconds and marks every answer on "
    "the map at the distance you had reached. When the answers stop, the "
    "screen flashes MESH CONNECTION LOST — you are standing on the edge of "
    "that node's reach.",
    "Use it BEFORE you commit to a site: to find out whether a spot you like "
    "can hear the node you already have, to check what a new antenna really "
    "bought you, or to prove a gap is real before building a node to fill "
    "it. Every ping it banks — the answers and the silences both — feeds the "
    "medic's own range model, so the next walk you do makes the advice on "
    "the map better.",
    "Two things are checked before it will start, and it says which it is "
    "waiting on. FIRST the node itself: Node Medic pings it, and if it does "
    "not answer it says so and goes no further — there is nothing to be "
    "learned by walking away from a box that is already silent. THEN a "
    "satellite fix, because distance is the whole measurement and with no "
    "GPS the walk would ping away for an hour and record nothing. Stand at "
    "the node, in the open, and wait for the start button to appear.",
    "Start it from the node's own page in VITALS, or from ANTENNA ▸ Range "
    "test if you are on site thinking about placement. Take the walk in as "
    "straight a line as the ground allows — a loop back towards the node "
    "measures the same short distance twice.",
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
     "About AU$21 each (US$11 from the maker, before postage)",
     ["The tiny XIAO ESP32-S3 + Wio-SX1262 kit. The medic builds it "
      "(RTNode-2400). A small antenna is in the box; a case is not.",
      "Most of a network should be made of these — they route and relay "
      "as well as a AU$60 board. What you give up is battery life, a "
      "screen, a case and GPS; not networking.",
      "The antenna is FREE: 8.2 cm of wire is a quarter-wave at 915 MHz. "
      "On the bench an 8 cm stub beat a 40 cm whip by a decibel and "
      "tied the best bought whip — 8 cm simply IS the right length for "
      "this band. What you pay for is weatherproofing, not reach.",
      "Power: mains, a USB power bank, or solar with a real panel — the "
      "ESP32 is not a low-power part.",
      "For people who solder: a bare ESP32 devkit + RFM95 module is about "
      "AU$7 in parts — but Node Medic cannot flash that build for you yet."]),
    ("2. The remote node — put it somewhere and leave it",
     "About AU$40 (AU$26–60 across boards)",
     ["An nRF52840 + SX1262 board ON ITS OWN, as a transport node. The "
      "RAK4631 kit (base board and antenna in the box) is the one to buy: "
      "an nRF52 — the lowest-power class of board the medic builds — and "
      "proven with Node Medic. The Heltec T114 is the other one the medic "
      "has built; it is imported.",
      "The radio board IS the node — it needs no computer attached. It "
      "sleeps at microamps and wakes for packets; listening costs about "
      "5–10 mA. Weeks on one 18650, indefinitely with a small panel: an "
      "18650 and holder are about AU$14, a 6 W panel and a 1S solar "
      "charger about AU$43.",
      "DO NOT bolt a Raspberry Pi to one of these to save battery. A Pi "
      "draws 10–20× what the radio does — that pairing is the dearest "
      "build AND the shortest-lived.",
      "The purpose-built solar ones (Heltec Mesh Solar, Seeed SenseCAP "
      "Solar Node) arrive with panel, battery and weatherproofing already "
      "solved — the medic has not built either yet."]),
    ("3. The message-holder — for people who are offline",
     "About AU$100–155 on mains (Pi Zero 2 W); AU$265–470 done properly "
     "on solar",
     ["A Raspberry Pi + an nRF52 radio board — the RAK4631 is the recommended "
      "pairing (its power draw measured on a Pi Zero 2 W; the full build through "
      "Node Medic is not yet bench-proven). The Pi Zero 2 W is the one to use, and it is often "
      "sold out; a Pi 4 or 5 works on mains from about AU$230 all up.",
      "ONLY this kind of node can hold messages for someone whose device "
      "is switched off. A transport node cannot.",
      "A network wants a few of these, not many — one in a hall, a shop, "
      "a home with power.",
      "ON SOLAR a Pi needs REAL hardware: a Pi Zero 2 W (about 17 Wh a "
      "day, measured), a 20 W panel minimum (40 W in Canberra or Hobart), "
      "about 60 Wh of USABLE battery for three cloudy days (a 10–12 Ah "
      "sealed lead-acid, or 6 Ah of LiFePO4), and a proper charge "
      "controller. A 5 W panel matches a perfect day and dies on the first "
      "cloudy one.",
      "If mains power is anywhere nearby, use it — the money is better "
      "spent on more everyday nodes."]),
]
BUILD_ALWAYS: List[str] = [
    "Right band: 915 MHz for Australia, NZ and the US; 868 for Europe. A "
    "433 MHz module looks identical and will never join your network.",
    "Fit an antenna BEFORE powering it — transmitting with none damages "
    "the radio.",
    "3.3 V to the radio. Never 5 V.",
    "Prices are Australian dollars before postage, checked October 2026. "
    "Raspberry Pi prices are rising and stock is patchy — check before you "
    "plan.",
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


def radio_lines(translate=None) -> List[str]:
    """The radio params as ready-to-read lines, e.g. 'Frequency — 915.125 MHz'.

    ``translate`` (optional) is applied to each label — the renderer passes
    ``ui.i18n.tr`` so the words come out in the operator's language while the
    numbers and units stay as they are. This module itself stays pure and
    English: the language is chosen at runtime, never at import.
    """
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
        name = translate(label) if translate is not None else label
        out.append(f"{name} — {v}{(' ' + unit) if unit else ''}".rstrip())
    return out
