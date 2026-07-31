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

#: Keyed by RTNODE_TARGETS key so the birth screen can look a target's photo up.
BOARDS: Dict[str, dict] = {
    "heltec_v4": {
        "label": "Heltec V4",
        "image": os.path.join(_DIR, "heltec_v4.png"),
        "has_screen": True,
        "oled": (0.413, 0.278, 0.793, 0.699),
    },
    "heltec_v3": {
        "label": "Heltec V3",
        "image": os.path.join(_DIR, "heltec_v3.png"),
        "has_screen": True,
        "oled": (0.378, 0.239, 0.835, 0.658),
    },
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
