"""Pure guided-birth flow data — NO Kivy, so the ordering/copy is unit-testable
(and CI, which has no Kivy, can import it).

The step LISTS live here: the intro paths, and the ordered steps per path. Each
step names its animation by a string KEY (resolved to a widget in
``ui.screens.birth_guide_screen``) so this stays free of any UI imports.
"""

from __future__ import annotations

#: What the operator can build — the intro chooser, ordered by rising complexity.
#: key -> (title, subtitle).
BIRTH_PATHS = [
    ("host", "A radio for phone or computer (RNode)",
     "Just flash a radio (RNode) to plug into a phone or computer you've already set up."),
    ("radio", "A standalone radio (RTNode-2400)",
     "A transport node on its own — reports its health back and is remotely repairable."),
    ("pi", "A Raspberry Pi + radio",
     "A propagation node - reports its health back and is remotely repairable, and "
     "can hold messages for users who aren't online. It can also mesh LoRa radio to "
     "Wi-Fi, Bluetooth and the internet. The best node to future-proof the network."),
]

#: The antenna-first step. It is NOT part of the guided lists — it's the very first
#: BIRTH screen (``birth_guide_screen._render_antenna``), shown BEFORE the detect
#: landing powers the board over USB, since that's when a missing antenna can fry
#: the radio. Every birth path reaches detect, so one landing covers them all.
ANTENNA_STEP = {
    "title": "Attach the antenna to the radio board",
    "body": "Connect the antenna to the radio board through its pigtail cable — you "
            "need BOTH ends attached. Push the tiny gold U.FL / IPEX plug straight "
            "down onto the matching socket on the radio board until it clicks, AND "
            "make sure a 915 MHz antenna (or one rated for your band) is screwed onto "
            "the SMA connector at the other end of the cable.",
    "hint": "The tiny U.FL / IPEX plug is fragile — connect it once and leave it. "
            "Repeated unplugging weakens the connector and it can snap off.",
    "warning": "Never power a radio board with no antenna attached — transmitting "
               "without one can permanently damage its radio (the power amplifier).",
    "anim": "connect_antenna",
}

#: Ordered guided steps per path. Each step: title, body, optional ``anim`` key
#: ("connect_board" | "insert_sd" | None) and optional ``hint`` / ``warning`` /
#: ``next`` label. The last step's Next hands off to the real BIRTH flow. The
#: antenna step is NOT here — it's the landing screen ahead of all of these
#: (see ``ANTENNA_STEP`` + ``birth_guide_screen._render_antenna``).
_STEPS = {
    "radio": [
        {"title": "Connect your radio board",
         "body": "Plug the radio board into Node Medic with a USB cable. Node Medic "
                 "powers it and will detect it automatically.",
         "hint": "Use a DATA USB cable — a charge-only cable won't be seen.",
         "anim": "connect_board"},
        {"title": "Node Medic configures it for you",
         "body": "After flashing, Node Medic joins your node's own setup Wi-Fi, sets "
                 "its name and radio settings, and puts it on your network — no manual "
                 "web portal needed.",
         "hint": "The medic briefly leaves your Wi-Fi to talk to the node, then rejoins.",
         "anim": "provision"},            # ANIMATION PLACEHOLDER — refine with the designer
        {"title": "Let's set it up",
         "body": "Node Medic will now detect the board, flash the firmware, then name "
                 "and configure the node automatically.",
         "anim": None, "next": "Start setup  →"},
    ],
    "pi": [
        {"title": "Insert the Pi's SD card into Node Medic",
         "body": "Put the Raspberry Pi's SD card into Node Medic's card reader so we "
                 "can write its operating system.",
         "anim": "insert_sd"},
        {"title": "Image the Pi",
         "body": "Now Node Medic writes Raspberry Pi OS to the card and sets its "
                 "name, Wi-Fi and login — a few details, no computer needed.",
         "anim": "insert_sd", "next": "Image the card  →", "screen": "pi_imager"},
        {"title": "Connect the radio board",
         "body": "Put the SD card into the Pi and power it on, then plug the radio "
                 "board into Node Medic with a USB cable.",
         "anim": "connect_board"},
        {"title": "Let's set it up",
         "body": "Node Medic will now provision the Pi and its radio, then walk you "
                 "through naming it.",
         "anim": None, "next": "Start setup  →"},
    ],
    "host": [
        {"title": "Connect the radio board",
         "body": "Plug the radio board into Node Medic with a USB cable so it can be "
                 "flashed as an RNode.",
         "hint": "Use a DATA USB cable — a charge-only cable won't be seen.",
         "anim": "connect_board"},
        {"title": "Let's flash it",
         "body": "Node Medic will detect the board and flash it as an RNode. Then "
                 "plug it into your phone or computer.",
         "anim": None, "next": "Start setup  →"},
    ],
}


def guide_steps(path):
    """The ordered step dicts for a birth *path* (pure — unit-testable). Unknown
    paths return an empty list. Returns a copy so callers can't mutate the source."""
    return [dict(s) for s in _STEPS.get(path, [])]
