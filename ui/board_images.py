"""Board photos + where each board's on-board screen sits in its photo.

Used by the birth screen to (a) show the operator a picture of the board they're
about to flash and (b) render the node's name live on the board's OLED — because
V3 and V4 look identical to Node Medic over USB, a picture is the clearest way to
confirm which one is in front of you.

``oled`` is the OLED's bounding box as fractions of the PHOTO (x0, y0, x1, y1)
with y measured from the TOP — so the name overlay lands on the little screen at
any display size. ``has_screen`` is False for boards with no display (then the
name is shown in a band above the photo instead).

Pure data + path helpers, no Kivy. Images live in assets/boards (tracked in git).
"""

from __future__ import annotations

import os
from typing import Dict, Optional

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "assets", "boards")

def _b(key: str, label: str, has_screen: bool = True, oled=None) -> dict:
    """A board entry whose photo lives at assets/boards/<key>.png. Entries are
    PLACEHOLDERS until the file exists — image_for() checks the filesystem, so
    dropping the PNG in upgrades that board from a text button to a photo card
    in the birth flow with no code change (operator plan 2026-08-01: photos
    arrive board-by-board as hardware is bought)."""
    d = {"label": label, "image": os.path.join(_DIR, f"{key}.png"),
         "has_screen": has_screen}
    if oled:
        d["oled"] = oled
    return d


#: Keyed by RTNODE_TARGETS key / rnode_boards catalogue key (see _ALIASES).
BOARDS: Dict[str, dict] = {
    # photos present in assets/boards/
    "heltec_v4": _b("heltec_v4", "Heltec V4",
                    oled=(0.413, 0.278, 0.793, 0.699)),
    "heltec_v3": _b("heltec_v3", "Heltec V3",
                    oled=(0.378, 0.239, 0.835, 0.658)),
    # placeholders — drop assets/boards/<key>.png in to activate the card
    "lora32_v21": _b("lora32_v21", "LilyGO LoRa32 v2.1"),
    "lora32_v20": _b("lora32_v20", "LilyGO LoRa32 v2.0"),
    "lora32_v10": _b("lora32_v10", "LilyGO LoRa32 v1.0"),
    "tbeam": _b("tbeam", "LilyGO T-Beam"),
    "heltec32_v2": _b("heltec32_v2", "Heltec LoRa32 v2"),
    "t3s3": _b("t3s3", "LilyGO LoRa T3S3"),
    "rak4631": _b("rak4631", "RAK4631", has_screen=False),
    "techo": _b("techo", "LilyGO T-Echo"),
    "tbeam_supreme": _b("tbeam_supreme", "LilyGO T-Beam Supreme"),
    "tdeck": _b("tdeck", "LilyGO T-Deck"),
    "heltec_t114": _b("heltec_t114", "Heltec Mesh Node T114"),
    "xiao_esp32s3": _b("xiao_esp32s3", "Seeed XIAO ESP32S3 (Wio-SX1262)",
                       has_screen=False),
    "heltec_wireless_tracker": _b("heltec_wireless_tracker",
                                  "Heltec Wireless Tracker"),
}


#: rnodeconf-catalogue keys -> our photo keys (same physical boards).
_ALIASES = {"heltec32_v3": "heltec_v3", "heltec32_v4": "heltec_v4"}


def get(key: str) -> Optional[dict]:
    return BOARDS.get(_ALIASES.get(key, key))


def image_for(key: str) -> Optional[str]:
    """The board photo path if we have it AND the file exists, else None."""
    b = get(key)
    if b and os.path.exists(b["image"]):
        return b["image"]
    return None


def has_screen(key: str) -> bool:
    b = get(key)
    return bool(b and b.get("has_screen"))


def label(key: str, default: str = "") -> str:
    b = get(key)
    return b["label"] if b else default


# --------------------------------------------------------------------------- #
# Raspberry Pi photos
# --------------------------------------------------------------------------- #

#: Where Pi artwork may live. Radio boards settled on assets/boards/<key>.png;
#: the first Pi photo predates that and sits in the animation folder. Search
#: both rather than move a file the animations already reference.
_PI_DIRS = (
    os.path.join(os.path.dirname(_DIR), "boards"),
    os.path.join(os.path.dirname(_DIR), "ui", "anim"),
)

#: Pis that are the same physical board, so they share a photo. The 3 A / 5 A
#: split is about the SUPPLY, not the hardware.
_PI_ALIASES = {"pi_5_full": "pi_5"}


def image_for_pi(pi_key: str) -> Optional[str]:
    """Path to a photo of this Raspberry Pi model, or None.

    Same drop-in contract as the boards: put ``assets/boards/<pi_key>.png`` in
    place and the flow starts using it, no code change. Missing art degrades to
    the generic Pi drawing — never to a photo of a DIFFERENT Pi, which would be
    worse than none at all, since the operator is using it to check what is in
    their hand (standing design aim, 2026-08-02).
    """
    key = (pi_key or "").strip()
    if not key:
        return None
    for candidate in (key, _PI_ALIASES.get(key, "")):
        if not candidate:
            continue
        for d in _PI_DIRS:
            path = os.path.join(d, f"{candidate}.png")
            if os.path.exists(path):
                return path
    return None


def pi_art_status() -> Dict[str, bool]:
    """{pi_key: has a photo} — so a screen can tell what it can illustrate."""
    keys = ("pi_zero_2w", "pi_3a_plus", "pi_3b_plus", "pi_4b", "pi_5",
            "pi_5_full")
    return {k: image_for_pi(k) is not None for k in keys}


# --------------------------------------------------------------------------- #
# "How to tell" — for boards that look alike
# --------------------------------------------------------------------------- #

#: Boards whose SIBLINGS are visually near-identical get a line naming the one
#: mark that actually differs. A whole-board photo is a strong confirmation when
#: boards look different and a WEAK one for a family that doesn't — the operator
#: glances, thinks "yes, that's mine", and is wrong. So for these, point at the
#: discriminating detail instead of the general resemblance (operator, 2026-08-02:
#: "am I wrong in thinking all those lilygo ones look almost exactly the same?").
#:
#: ONLY verified markings belong here. Entries below are read off LilyGO's own
#: documentation and product photography; anything unverified is left out rather
#: than guessed, the same rule the chip-variant table follows.
HOW_TO_TELL: Dict[str, str] = {
    # Verified against wiki.lilygo.cc/products/t3-series/t3-lora32/ and LilyGO's
    # own two-sided product photo. The board is SOLD as "LoRa32 v2.1" but that
    # string appears NOWHERE on it — a real source of confusion, so say it.
    "lora32_v21": ("Check the white label: MODEL: T3 V1.6.1 (the back is "
                   "silkscreened T3_V1.6). It will NOT say v2.1 anywhere."),
}


def how_to_tell(board_key: str) -> str:
    """The one mark that distinguishes this board from its look-alikes, or ""."""
    return HOW_TO_TELL.get((board_key or "").strip(), "")
