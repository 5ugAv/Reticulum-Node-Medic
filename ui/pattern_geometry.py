"""Where the nine dots sit, and which one a finger is on — pure geometry.

Kept out of the widget so it can be tested without a display. The medic's whole
lock rests on this: a hit radius that is too small makes the pattern feel broken
with cold hands in the field, and one that is too large picks up dots the
operator did not mean, which they will not notice until the vault refuses them.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

#: 3x3, indexed 0..8 left to right, TOP row first — the phone convention, and
#: the order a person describes a pattern out loud.
COLS = ROWS = 3

#: Fraction of the cell used as the touch target. 0.5 gives a target the size of
#: a fingertip on a 7" screen with a gap between neighbours, so a slightly
#: sloppy diagonal does not silently collect a dot it passed near.
HIT_FRACTION = 0.5


def dot_centres(x: float, y: float, width: float, height: float
                ) -> List[Tuple[float, float]]:
    """Centre of each dot, index 0..8, for a pad occupying the given box.

    Row 0 is the TOP row, because that is where a person starts. Kivy's y grows
    upward, so the top row has the largest y — getting this backwards mirrors
    every pattern vertically and the vault stops opening.
    """
    cw = width / COLS
    ch = height / ROWS
    out = []
    for row in range(ROWS):
        for col in range(COLS):
            cx = x + cw * (col + 0.5)
            cy = y + height - ch * (row + 0.5)
            out.append((cx, cy))
    return out


def hit_radius(width: float, height: float) -> float:
    """Radius of a dot's touch target, from the smaller cell dimension.

    HIT_FRACTION is the target's DIAMETER as a fraction of the cell, so the
    radius is half of it — at 0.5 the targets fill half each cell and never
    touch their neighbours.
    """
    return min(width / COLS, height / ROWS) * HIT_FRACTION / 2.0


def dot_at(px: float, py: float, x: float, y: float,
           width: float, height: float) -> Optional[int]:
    """The dot index under a touch, or None if it is between dots.

    Nearest-centre within the hit radius, so a touch in the gap belongs to
    nobody rather than to whichever dot the loop happened to reach first.
    """
    r = hit_radius(width, height)
    best = None
    best_d2 = r * r
    for i, (cx, cy) in enumerate(dot_centres(x, y, width, height)):
        d2 = (px - cx) ** 2 + (py - cy) ** 2
        if d2 <= best_d2:
            best, best_d2 = i, d2
    return best


def extend(path: List[int], dot: Optional[int]) -> List[int]:
    """Add *dot* to a pattern in progress, ignoring repeats and misses.

    A finger dragged across the pad reports many touches per dot, and drifts
    back over dots it already used. Both are the same answer: the path is
    unchanged. Returns a NEW list so a caller cannot mutate a path it is
    also comparing against.
    """
    if dot is None or dot in path:
        return list(path)
    return list(path) + [dot]
