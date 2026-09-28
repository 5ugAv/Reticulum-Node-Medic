"""Tap zones for the front-page poster — pure geometry, no Kivy.

The designed front page (assets/ui/front_page.png, 720x1280 — native panel
size) carries five full-bleed mode cards along the bottom (VITALS / MAPS /
BUILD / ANTENNA / CHAT — POSTER_CARD_LABELS below is the tested claim) and,
on the globe above them, four connection markers standing on the centre
meridian: LORA, WI-FI, BLUETOOTH, INTERNET, top to bottom by reach. Zones are
expressed in image-fraction coordinates (x rightward, y DOWNWARD from the
top-left, 0..1) so they survive any scaling; the Kivy screen converts touches
into this space and asks ``zone_at``.

The LORA trunk node — the filled disc the whole mesh grows out of — is the
Easter egg: it opens the credits screen (the people who made the tool, and why
Reticulum matters to communities). Mitosis lives under BIRTH now.

EVERY COORDINATE BELOW WAS MEASURED OFF THE PAINTING, not chosen. The two
emblem zones drifted silently through five repaints between 2026-09-13 and
2026-09-28 — at the end of that the credits circle had wandered onto blank
mesh and then onto the INTERNET marker, so tapping "INTERNET" opened the
credits. test_front_page_vocabulary.py now pins both against the shipped PNG
the same way it pins the card labels, which is the only thing that stops a
sixth drift.
"""

from __future__ import annotations

from typing import Optional

# Bottom card row: five equal columns between the side margins.
# Measured off the 2026-09-28 painting: the drawn key tops sit at y 1004 of
# 1280. The zone meets the ART rather than the other way round — the artist
# was asked three times for 1011 and the row wandered 1006 / 1003 / 1005, so
# the 0.6 mm was closed from this side instead.
CARDS_TOP = 0.784         # y-fraction where the card row begins
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
# Repainted 2026-09-13 to the settled vocabulary (docs/FRONT_PAGE_BRIEF.md):
# a stranger can guess MAPS, BUILD and ANTENNA; nobody ever guessed TRIAGE.
# The SCREENS keep their internal names — the painted word is the operator's
# word, and this map is the single place the two are tied together.
POSTER_CARD_LABELS = ["VITALS", "MAPS", "BUILD", "ANTENNA", "CHAT"]
POSTER_WORD_FOR = {"vitals": "VITALS", "scan": "MAPS", "birth": "BUILD",
                   "triage": "ANTENNA", "chat": "CHAT"}

# The LORA trunk node — the Easter egg (credits). Measured: the filled disc
# centres at (359.5, 217.5) of 720x1280. The tap radius is deliberately wider than
# the painted disc (0.075 vs the disc's own 0.042) so it is a thumb-sized
# target, and it still clears the WI-FI box below by 61 px.
CROSS_CX = 0.499
CROSS_CY = 0.170          # y-fraction, top-down
CROSS_R = 0.075           # radius in x-fractions (aspect-corrected below)
IMAGE_ASPECT = 720 / 1280

# The WI-FI marker (ring + label, second down the meridian) is a second Easter
# egg: a shortcut into WiFi settings, mirroring the gear icon. Measured: the
# ring and its label span x 0.472-0.618, y 0.262-0.300; the box below pads that
# a little without reaching the BLUETOOTH marker beneath it.
WIFI_LEFT = 0.465
WIFI_RIGHT = 0.625
WIFI_TOP = 0.255          # y-fraction, top-down
WIFI_BOTTOM = 0.308


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


def rect_to_widget(rect, img_x: float, img_y: float, img_w: float, img_h: float):
    """An image-fraction rect -> on-screen pixels, in Kivy's bottom-up space.

    ``rect`` is ``(left, top, w, h)`` as ``card_rect`` returns it, with *top*
    measured DOWNWARD from the image's top edge. ``img_*`` describe where the
    poster actually landed on screen — the letterboxed box, not the widget.

    Returns ``(x, y, w, h)`` with *y* measured UP from the bottom, which is what
    every Kivy canvas instruction wants. The single flip lives here rather than
    in the screen so the pressed-key highlight and the tour's crop cannot
    disagree about which way up the poster is.
    """
    left, top, w, h = rect
    return (img_x + left * img_w,
            img_y + (1.0 - top - h) * img_h,
            w * img_w,
            h * img_h)


def press_fires(mode, down_zone) -> bool:
    """Should releasing on ``mode`` open it?

    ``down_zone`` is the card the finger went DOWN on, or None when no press was
    seen. A card only fires when press and release are on the same one, so
    sliding off cancels the way a physical key does.

    The None case is the important one and it is deliberately permissive. If the
    press is never seen — ``on_touch_down`` didn't reach the screen, or the
    poster hasn't been measured yet — the old behaviour stands and the tap
    opens. A front page that quietly stops navigating is a dead medic; a stray
    tap is an annoyance. The failure has to fall on the annoying side.

    Zones with no painted card (the credits emblem, the WI-FI shortcut) have no
    pressed state to match against, so they always fire.
    """
    if not mode:
        return False
    if mode not in CARD_ORDER:
        return True
    if down_zone is None:
        return True
    return down_zone == mode
