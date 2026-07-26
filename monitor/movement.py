"""Detect that the medic is on the move (from its GPS fixes) so it can drop out
of transport/propagation automatically.

A moving node that routes/relays is bad for the mesh: other nodes pick paths
through it and it announces those paths, so as it travels the paths break and the
announces churn — wasted airtime and unstable routing. If the medic senses it's
moving, it should fall back to *backpack* mode (transport off) until it settles.

This module is the pure, unit-testable core: feed it GPS fixes and it tells you
the moment SUSTAINED movement away from the settled spot is confirmed. It is
deliberately ONE-WAY — it only ever reports 'moving' (→ switch to backpack). It
never says 'stopped, resume transport': bringing a routing role up at a random
resting place is exactly what we're avoiding, so returning to Home mode stays a
deliberate, manual choice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

#: How far the fix must sit from the settled anchor to count as movement. Well
#: beyond ordinary GPS jitter (~5-15 m stationary) so wander can't trip it — a
#: genuine relocation, not the fix breathing in place.
DEFAULT_MOVE_THRESHOLD_M = 150.0

#: Consecutive over-threshold fixes required before movement is trusted. Rejects a
#: single wild fix (a held/low-sat reading can jump) — it must persist.
DEFAULT_CONFIRM = 2


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two (lat, lon) points, in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class MovementDetector:
    """Holds a settled 'anchor' position and reports confirmed movement away from
    it. Feed every GPS fix to :meth:`update`; it returns True the instant movement
    is confirmed (and re-anchors at the new spot, so a continued walk asserts
    'moving' once per ~threshold rather than spamming every fix).

    Displacement is always measured from the ANCHOR, not the previous fix, so many
    small hops that never individually clear the threshold still trip it once they
    add up — you can't creep the unit away unnoticed."""
    threshold_m: float = DEFAULT_MOVE_THRESHOLD_M
    confirm: int = DEFAULT_CONFIRM
    anchor: Optional[Tuple[float, float]] = None
    _over: int = 0

    def update(self, lat: Optional[float], lon: Optional[float]) -> bool:
        """Feed a new fix. True == movement just confirmed (caller: go backpack)."""
        if lat is None or lon is None:
            return False
        if self.anchor is None:                     # first fix — settle here
            self.anchor = (lat, lon)
            self._over = 0
            return False
        d = haversine_m(self.anchor[0], self.anchor[1], lat, lon)
        if d >= self.threshold_m:
            self._over += 1
            if self._over >= self.confirm:          # sustained → confirmed
                self.anchor = (lat, lon)            # re-anchor at the new spot
                self._over = 0
                return True
            return False
        self._over = 0                              # back inside jitter — settled
        return False

    def reset(self, lat: Optional[float] = None, lon: Optional[float] = None):
        """Re-settle the anchor (e.g. after a MANUAL mode change), so the next trip
        is measured from here — auto-detect then won't immediately override a
        deliberate choice. With no coords, forgets the anchor (re-settles on the
        next fix)."""
        self.anchor = (lat, lon) if lat is not None and lon is not None else None
        self._over = 0
