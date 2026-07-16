"""First-link range discovery — the guided birth of a mesh (backlog/BIRTH flow).

The first node a user builds becomes **home base**. For the second node, the
tool runs a walk-in protocol that turns "how far does my hardware actually
reach?" from a guess into a measurement:

1. the user picks a desired spot (within 10 km of home) and births the node;
2. test the link to home base — no connection? the tool suggests a spot 1 km
   closer to home on the map; move, test again;
3. repeat until a connection is made; acknowledge it, then check it's STRONG
   enough (a permanent link needs margin, not a fluke decode) — too weak keeps
   stepping closer;
4. the final connected distance is the user's first measured reach metric for
   the hardware they chose — and it seeds the placement engine's observed
   reach, so all future "add a node here" suggestions start from evidence.

Pure state machine (injected test results, no hardware); the BIRTH screen
renders its guidance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAX_START_KM = 10.0        # the desired spot must be within this of home
STEP_KM = 1.0              # each failed test moves this much closer to home
#: "Strong enough" for a permanent home link: solidly in the GOOD band, not a
#: marginal fluke. (SF9 decode floor is -12.5 dB; we want real margin.)
MIN_LINK_SNR_DB = 0.0
HOME_ARRIVED_KM = 0.25     # within this of home with no link = hardware problem


def _km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _toward(lat, lon, home_lat, home_lon, step_km) -> Tuple[float, float]:
    """The point *step_km* from (lat, lon) along the straight line to home
    (linear interpolation — fine at these distances)."""
    total = _km(lat, lon, home_lat, home_lon)
    if total <= step_km:
        return (home_lat, home_lon)
    f = step_km / total
    return (lat + (home_lat - lat) * f, lon + (home_lon - lon) * f)


@dataclass
class FirstLinkSession:
    """State machine for the second-node walk-in. Feed it test results; it
    answers with the next target and plain-English guidance."""

    home_lat: float
    home_lon: float
    state: str = "await_spot"     # await_spot | testing | connected_weak | done | failed
    target: Optional[Tuple[float, float]] = None
    attempts: List[dict] = field(default_factory=list)
    reach_km: Optional[float] = None
    final_snr: Optional[float] = None

    # -- step 1: the desired spot -------------------------------------------

    def start(self, lat: float, lon: float) -> dict:
        d = _km(lat, lon, self.home_lat, self.home_lon)
        if d > MAX_START_KM:
            self.target = _toward(lat, lon, self.home_lat, self.home_lon,
                                  d - MAX_START_KM)
            self.state = "testing"
            return self._say(
                f"That spot is {d:.1f} km from home base - too far for a first "
                f"link test. Start within {MAX_START_KM:.0f} km: try the "
                "suggested spot on the map.", suggest=True)
        self.target = (lat, lon)
        self.state = "testing"
        return self._say(
            f"Good - {d:.1f} km from home base. Birth the node here, then run "
            "the link test.", suggest=True)

    # -- step 2..n: test results ---------------------------------------------

    def report_test(self, connected: bool, snr_db: Optional[float] = None) -> dict:
        if self.state not in ("testing", "connected_weak"):
            return self._say("Start by choosing a spot for the new node.")
        here = self.target
        dist = _km(here[0], here[1], self.home_lat, self.home_lon)
        self.attempts.append({"lat": here[0], "lon": here[1], "km": round(dist, 2),
                              "connected": connected, "snr_db": snr_db})

        if connected and snr_db is not None and snr_db >= MIN_LINK_SNR_DB:
            self.state = "done"
            self.reach_km = round(dist, 2)
            self.final_snr = snr_db
            return self._say(
                f"Connected, and the link is strong (clarity {snr_db:+.1f} dB) "
                f"at {dist:.1f} km. That's your hardware's first measured "
                "reach - the map will use it when suggesting future nodes. "
                "Secure this node!")

        if connected:
            self.state = "connected_weak"
            reason = (f"Connected, but the link is weak (clarity "
                      f"{snr_db:+.1f} dB)" if snr_db is not None
                      else "Connected, but the link looks weak")
        else:
            reason = "No connection to home base"

        nxt = _toward(here[0], here[1], self.home_lat, self.home_lon, STEP_KM)
        nxt_dist = _km(nxt[0], nxt[1], self.home_lat, self.home_lon)
        if dist <= HOME_ARRIVED_KM or nxt_dist <= HOME_ARRIVED_KM and not connected:
            self.state = "failed"
            return self._say(
                "You're practically at home base and still can't get a solid "
                "link - this isn't about distance. Check both antennas, then "
                "run Probe on each node.")
        self.target = nxt
        self.state = "testing"
        return self._say(
            f"{reason}. Move about {STEP_KM:.0f} km closer to home - head to "
            f"the suggested spot on the map ({nxt_dist:.1f} km out) and test "
            "again.", suggest=True)

    # -- outcome ---------------------------------------------------------------

    def result(self) -> Optional[dict]:
        """The measured-reach record once done — seeds placement's observed
        reach and goes on the birth certificate."""
        if self.state != "done":
            return None
        return {"reach_km": self.reach_km, "final_snr_db": self.final_snr,
                "attempts": len(self.attempts)}

    def _say(self, text: str, suggest: bool = False) -> dict:
        out = {"state": self.state, "guidance": text}
        if suggest and self.target is not None:
            out["suggested_spot"] = {"lat": self.target[0], "lon": self.target[1]}
        return out
