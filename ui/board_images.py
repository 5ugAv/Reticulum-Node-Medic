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
