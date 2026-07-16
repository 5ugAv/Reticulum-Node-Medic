"""Interference source log — backlog feature 5 (data core).

Whenever a TRIAGE session sees a noise floor above the "degraded" threshold at
a located spot, an entry is recorded: where, when, how bad. The log feeds the
SCAN map (warning markers), and the placement suggester uses it to steer
suggested sites away from known-noisy ground.

Pure data store + queries; persists inside the monitoring DB that MITOSIS
copies. Entries live until manually removed (long-press on the map).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import List, Optional

NOISE_LOG_THRESHOLD_DBM = -105     # "degraded" — at/above this, Triage logs it
NEARBY_M = 200.0                   # suggestions within this of a log entry get flagged


@dataclass
class InterferenceEntry:
    t: float                       # epoch seconds
    lat: float
    lon: float
    noise_floor_dbm: int
    gps_accuracy_m: Optional[float] = None
    rssi_dbm: Optional[int] = None
    snr_db: Optional[float] = None
    composite_score: Optional[float] = None
    session_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _metres(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class InterferenceLog:
    def __init__(self):
        self.entries: List[InterferenceEntry] = []

    def maybe_log(self, noise_floor_dbm: int, lat: Optional[float],
                  lon: Optional[float], t: float, **extra) -> Optional[InterferenceEntry]:
        """Record IF the reading is at/above the degraded threshold and the spot
        is located (no GPS fix -> nothing to put on a map -> no entry)."""
        if noise_floor_dbm < NOISE_LOG_THRESHOLD_DBM:
            return None
        if lat is None or lon is None:
            return None
        entry = InterferenceEntry(t=t, lat=lat, lon=lon,
                                  noise_floor_dbm=noise_floor_dbm, **extra)
        self.entries.append(entry)
        return entry

    def remove(self, entry: InterferenceEntry) -> None:
        """Manual delete (long-press a marker on the map)."""
        self.entries = [e for e in self.entries if e is not entry]

    def near(self, lat: float, lon: float, radius_m: float = NEARBY_M
             ) -> List[InterferenceEntry]:
        """Entries within *radius_m* of a point — closest first."""
        hits = [(e, _metres(lat, lon, e.lat, e.lon)) for e in self.entries]
        hits = [(e, d) for e, d in hits if d <= radius_m]
        hits.sort(key=lambda p: p[1])
        return [e for e, _d in hits]

    def caution_for(self, lat: float, lon: float,
                    radius_m: float = NEARBY_M) -> Optional[str]:
        """A plain-English warning if a suggested location sits near logged
        interference, or None when the ground is clean."""
        hits = self.near(lat, lon, radius_m)
        if not hits:
            return None
        e = hits[0]
        import datetime
        day = datetime.datetime.fromtimestamp(
            e.t, datetime.timezone.utc).strftime("%d %b %Y")
        dist = _metres(lat, lon, e.lat, e.lon)
        return (f"Interference was logged {dist:.0f}m from this location on "
                f"{day} (noise floor {e.noise_floor_dbm} dBm). Triage is "
                "strongly recommended before deploying here.")

    # -- persistence (rides in the monitoring DB) ---------------------------

    def to_dict(self) -> dict:
        return {"entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, data: dict) -> "InterferenceLog":
        log = cls()
        for e in (data or {}).get("entries", []):
            log.entries.append(InterferenceEntry(**e))
        return log
