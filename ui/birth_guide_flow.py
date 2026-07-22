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
     "A Pi running Reticulum with an attached radio (a propagation / host node)."),
]

#: Ordered guided steps per path. Each step: title, body, optional ``anim`` key
#: ("connect_board" | "insert_sd" | None) and optional ``hint`` / ``next`` label.
#: The last step's Next hands off to the real BIRTH flow.
_STEPS = {
    "radio": [
        {"title": "Connect your radio board",
         "body": "Plug the radio board into Node Medic with a USB cable. Node Medic "
                 "powers it and will detect it automatically.",
         "hint": "Use a DATA USB cable — a charge-only cable won't be seen.",
         "anim": "connect_board"},
        {"title": "Let's set it up",
         "body": "Node Medic will now detect the board, then walk you through naming "
                 "it and flashing the firmware.",
         "anim": None, "next": "Start setup  →"},
    ],
    "pi": [
        {"title": "Insert the Pi's SD card",
         "body": "Put the Raspberry Pi's SD card into Node Medic's card reader so we "
                 "can write its operating system.",
         "anim": "insert_sd"},
        {"title": "Image the Pi",
         "body": "Next we'll write Raspberry Pi OS to the card and set its name, "
                 "Wi-Fi and password — a few details at a time.",
         "hint": "SD imaging on the medic is coming — for now, image the card with "
                 "Raspberry Pi Imager, then continue.",
         "anim": None},
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
