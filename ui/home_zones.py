"""Tap zones for the front-page poster — pure geometry, no Kivy.

The designed front page (assets/ui/front_page.png, 720x1280 — native panel
size) carries five full-bleed mode cards along the bottom (VITALS / SCAN /
BIRTH / TRIAGE / PROBE) and the red cross emblem at its heart. Zones are expressed in image-fraction coordinates
(x rightward, y DOWNWARD from the top-left, 0..1) so they survive any scaling;
the Kivy screen converts touches into this space and asks ``zone_at``.

The red cross is the Easter egg: it opens the credits screen (the people who
made the tool, and why Reticulum matters to communities). Mitosis lives under
BIRTH now.
"""

from __future__ import annotations

from typing import Optional

# Bottom card row: five equal columns between the side margins.
CARDS_TOP = 0.79          # y-fraction where the card row begins
CARDS_LEFT = 0.0
CARDS_RIGHT = 1.0   # the 720x1280 cut runs the cards full-bleed
CARD_ORDER = ["vitals", "scan", "birth", "triage", "chat"]

#: WHAT THE POSTER ACTUALLY SAYS, read off assets/ui/front_page.png. The home
#: screen is a TAP-MAP over a painted image: this list is not a menu the tool
#: renders, it is a claim about words already printed on the artwork.
#:
#: So CARD_ORDER cannot be reordered or re-pointed in code alone. Changing the
#: fifth entry to "chat" — which #22/#23 want — would leave a card labelled
#: "PROBE / DIAGNOSE & REPAIR", illustrated with a toolbox, opening a chat
#: screen. That is the operator's standing rule broken at its most literal:
#: everything the operator sees must correspond to what it does, and here the
#: mismatch would be in WORDS rather than a picture.
#:
#: The fifth card became CHAT on 2026-08-07: the operator supplied artwork in
#: the poster's own style, it was composited into the row, and the zone followed
#: it in the same commit. PROBE keeps its own door in the sidebar, so nothing
#: became unreachable — Self Diagnose in particular.
#:
#: A test holds these two in step so the mismatch cannot be introduced by an
#: innocent-looking one-line edit.
POSTER_CARD_LABELS = ["VITALS", "SCAN", "BIRTH", "TRIAGE", "CHAT"]

# The red-cross emblem — the Easter egg (credits).
CROSS_CX = 0.50
CROSS_CY = 0.46           # y-fraction, top-down
CROSS_R = 0.13            # radius in x-fractions (aspect-corrected below)
IMAGE_ASPECT = 720 / 1280

# The WI-FI emblem (the red fan icon + label in the connectivity stack) is a
# second Easter egg: a shortcut into WiFi settings, mirroring the gear icon.
# Its box sits between the LORA and BLUETOOTH labels, clear of the cross circle.
WIFI_LEFT = 0.42
WIFI_RIGHT = 0.58
WIFI_TOP = 0.25           # y-fraction, top-down
WIFI_BOTTOM = 0.335


def zone_at(fx: float, fy: float) -> Optional[str]:
    """Mode name for a tap at image-fraction (fx, fy), or None.
    fy is measured DOWNWARD from the image's top edge."""
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return None
    if fy >= CARDS_TOP and CARDS_LEFT <= fx <= CARDS_RIGHT:
        span = (CARDS_RIGHT - CARDS_LEFT) / len(CARD_ORDER)
        idx = int((fx - CARDS_LEFT) / span)
        return CARD_ORDER[min(idx, len(CARD_ORDER) - 1)]
    # the WI-FI emblem shortcuts into WiFi settings (like the gear icon)
    if WIFI_LEFT <= fx <= WIFI_RIGHT and WIFI_TOP <= fy <= WIFI_BOTTOM:
        return "wifi"
    # circle test: convert the y-offset into x-fraction units (the image is
    # 1.5x taller than wide) so the tap radius is circular on screen
    dx = fx - CROSS_CX
    dy_x = (fy - CROSS_CY) / IMAGE_ASPECT
    if (dx * dx + dy_x * dy_x) ** 0.5 <= CROSS_R:
        return "credits"
    return None


#: When the word on the card is not the screen's internal name. CHAT is painted
#: on the poster; the screen that answers it has been called "comms" since it
#: was built (ui/screens/comms_screen.py — "the mesh messenger for your
#: pocket"). Renaming the screen would have churned every reference for a label;
#: this states the translation in one place instead.
ZONE_SCREEN = {"chat": "comms"}


def screen_for(zone: str) -> str:
    """The screen a tapped zone should open. Identity unless ZONE_SCREEN says
    otherwise."""
    return ZONE_SCREEN.get(zone, zone)


def card_rect(zone: str):
    """``(left, top, width, height)`` of a poster card, in image fractions with
    *top* measured DOWNWARD — or None for a zone that has no painted card.

    Added for the first-use tour ([[show-dont-tell-ux]]): the screen that
    explains BIRTH shows the operator the actual painted BIRTH card, cropped out
    of the front page, rather than a sentence describing where to find it. They
    then look for a picture they have already seen instead of a word they have
    to match.

    It reads the SAME constants ``zone_at`` divides the row by, so a card that
    moves in the artwork moves in the tour with it. Computing the crop from its
    own copy of the numbers is how a tour ends up pointing confidently at the
    card next door.

    Returns None — never a guess — for PROBE, MITOSIS and Settings, which have
    no card on the poster. A tour screen showing a neighbouring card because
    something had to be shown is the wrong-picture failure this rule exists to
    stop.
    """
    if zone not in CARD_ORDER:
        return None
    span = (CARDS_RIGHT - CARDS_LEFT) / len(CARD_ORDER)
    left = CARDS_LEFT + span * CARD_ORDER.index(zone)
    return (left, CARDS_TOP, span, 1.0 - CARDS_TOP)
