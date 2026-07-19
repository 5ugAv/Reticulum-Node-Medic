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
CARD_ORDER = ["vitals", "scan", "birth", "triage", "probe"]

# The red-cross emblem — the Easter egg (credits).
CROSS_CX = 0.50
CROSS_CY = 0.46           # y-fraction, top-down
CROSS_R = 0.13            # radius in x-fractions (aspect-corrected below)
IMAGE_ASPECT = 720 / 1280


def zone_at(fx: float, fy: float) -> Optional[str]:
    """Mode name for a tap at image-fraction (fx, fy), or None.
    fy is measured DOWNWARD from the image's top edge."""
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return None
    if fy >= CARDS_TOP and CARDS_LEFT <= fx <= CARDS_RIGHT:
        span = (CARDS_RIGHT - CARDS_LEFT) / len(CARD_ORDER)
        idx = int((fx - CARDS_LEFT) / span)
        return CARD_ORDER[min(idx, len(CARD_ORDER) - 1)]
    # circle test: convert the y-offset into x-fraction units (the image is
    # 1.5x taller than wide) so the tap radius is circular on screen
    dx = fx - CROSS_CX
    dy_x = (fy - CROSS_CY) / IMAGE_ASPECT
    if (dx * dx + dy_x * dy_x) ** 0.5 <= CROSS_R:
        return "credits"
    return None
