"""The boundary walk — range truth by walking away (operator spec 2026-08-13).

The operator stands at a node with the medic, starts the walk, and walks.
Every :data:`PING_EVERY_S` the medic pings the node over the mesh; every
answer and every silence becomes a sample at a GPS-known distance. When the
link drops for :data:`LOST_AFTER_MISSES` consecutive pings the screen flashes
MESH CONNECTION LOST — the operator has found the boundary, which is the
point of the exercise. Hits become positive :class:`LinkObservation` evidence,
misses become :class:`LinkFailure` negative evidence — the two halves
``estimate_range`` has waited on since phase 3 (this module is the first
producer for ``walk_failures``, which was built for it a month early).

Pure state machine, sibling of :mod:`monitor.first_link`: injected clock,
injected GPS, injected ping outcomes. The MAPS screen renders it; nothing
here touches hardware. One miss is NOT a lost mesh (a single LoRa frame can
die of anything), so the banner waits for the second consecutive silence —
but every miss is recorded evidence regardless: the model wants the truth,
the banner wants certainty.
"""

from __future__ import annotations

import json
import math
import os
import time as _time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from monitor.synapse_links import LinkObservation, POSITION_EXACT
from monitor.synapse_range import LinkFailure, walk_failures

#: Ping cadence — the operator's own number (2026-08-13: "ping every 20s").
PING_EVERY_S = 20.0
#: Consecutive silences before the banner declares the boundary found.
LOST_AFTER_MISSES = 2

_WALK_DIR = "~/.reticulum-node-medic"
_OBS_FILE = "walk_observations.jsonl"
_FAIL_FILE = "walk_failures.jsonl"


def _km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class BoundaryWalkSession:
    """Feed it pings and fixes; it answers with state, story and evidence."""

    node_key: str
    node_name: str
    node_lat: Optional[float]
    node_lon: Optional[float]
    now: float
    state: str = "linked"          # linked | lost — hopeful until proven not
    samples: List[dict] = field(default_factory=list)
    max_linked_km: float = 0.0
    _misses: int = 0
    _last_ping_at: Optional[float] = None
    _pinging: bool = False

    # -- cadence ------------------------------------------------------------

    def due(self, now: float) -> bool:
        """Time for the next ping? First one fires immediately; never while
        one is still in flight (a LoRa round trip can outlive the cadence)."""
        if self._pinging:
            return False
        if self._last_ping_at is None:
            return True
        return (now - self._last_ping_at) >= PING_EVERY_S

    def begin_ping(self, now: float) -> None:
        self._last_ping_at = now
        self._pinging = True

    # -- results ------------------------------------------------------------

    def _anchor(self) -> Optional[Tuple[float, float]]:
        if self.node_lat is not None and self.node_lon is not None:
            return (self.node_lat, self.node_lon)
        return None

    def ping_result(self, now: float, ok: bool,
                    snr_db: Optional[float] = None,
                    gps: Optional[Tuple[float, float]] = None) -> dict:
        """One outcome in. GPS may be None (no fix): the outcome still counts
        for the story, but carries no distance and feeds no range evidence —
        a measurement without a place measures nothing about reach."""
        self._pinging = False
        km = None
        lat = lon = None
        if gps is not None:
            lat, lon = gps
            if self._anchor() is None:
                # The walk starts AT the node (spec: "walk away from it") —
                # the first fix becomes the anchor when the registry holds
                # no position for the node.
                self.node_lat, self.node_lon = lat, lon
            km = _km(lat, lon, self.node_lat, self.node_lon)
        sample = {"t": now, "lat": lat, "lon": lon, "km": km,
                  "connected": bool(ok), "snr_db": snr_db}
        self.samples.append(sample)
        if ok:
            self._misses = 0
            self.state = "linked"
            if km is not None and km > self.max_linked_km:
                self.max_linked_km = km
        else:
            self._misses += 1
            if self._misses >= LOST_AFTER_MISSES:
                self.state = "lost"
        return sample

    # -- what the screen shows ----------------------------------------------

    def banner(self) -> Tuple[str, bool]:
        """(text, flashing). Flashing only at the found boundary — the
        operator asked for yellow/black flashing at the loss, and a banner
        that flashes for anything less teaches eyes to ignore it."""
        if self.state == "lost":
            return ("MESH CONNECTION LOST", True)
        hits = sum(1 for s in self.samples if s["connected"])
        return (f"Linked — {hits} pings good, "
                f"furthest {self.max_linked_km:.2f} km", False)

    def summary(self) -> str:
        hits = sum(1 for s in self.samples if s["connected"])
        misses = len(self.samples) - hits
        return (f"{hits} pings answered, {misses} silent; furthest linked "
                f"point {self.max_linked_km:.2f} km from {self.node_name}.")

    # -- evidence ------------------------------------------------------------

    def evidence(self, medic_id: str = "MEDIC"
                 ) -> Tuple[List[LinkObservation], List[LinkFailure]]:
        """Everything the walk proved, in the range model's two currencies.
        Only located samples qualify — see ping_result."""
        obs = [LinkObservation(
                   heard_by=medic_id, heard_from=self.node_key,
                   observed_at=s["t"], snr_db=s["snr_db"],
                   distance_km=s["km"],
                   heard_by_position=POSITION_EXACT,
                   heard_from_position=POSITION_EXACT,
                   source="boundary_walk")
               for s in self.samples if s["connected"] and s["km"] is not None]
        fails = walk_failures(
            [s for s in self.samples if s["km"] is not None],
            observed_at=self.samples[-1]["t"] if self.samples else 0.0)
        return obs, fails


#: How recently a node must have been heard to be offered as a walk target.
#: "Currently connected" has to mean something checkable (operator,
#: 2026-09-19): a node silent for days would send the operator walking away
#: from something that was never going to answer. Generous enough to cover a
#: six-hourly beacon cadence, short enough to mean something.
WALK_CANDIDATE_MAX_AGE_H = 12.0


def walkable_nodes(registry, now: float,
                   max_age_h: float = WALK_CANDIDATE_MAX_AGE_H) -> List[dict]:
    """The nodes a boundary walk could be run against right now, freshest
    first — each as ``{name, dst_hash, heard_hours, lat, lon}``.

    A candidate needs two things and both are checked, never assumed: a mesh
    address to ping, and a sighting recent enough that pinging it is honest.
    """
    out = []
    try:
        records = list(registry.all(now))
    except Exception:                                              # noqa: BLE001
        return []
    for rec in records:
        dst = (getattr(rec, "dst_hash", "") or "").strip()
        if not dst:
            continue                       # nothing to ping
        try:
            hours = rec.last_seen_hours(now)
        except Exception:                                          # noqa: BLE001
            hours = None
        if hours is None or hours > max_age_h:
            continue                       # never heard, or long silent
        out.append({"name": getattr(rec, "name", "") or dst[:8],
                    "dst_hash": dst, "heard_hours": hours,
                    "lat": getattr(rec, "lat", None),
                    "lon": getattr(rec, "lon", None)})
    out.sort(key=lambda n: n["heard_hours"])
    return out


# -- persistence: the first real producer for the range model ---------------

def append_evidence(obs: List[LinkObservation], fails: List[LinkFailure],
                    base_dir: str = _WALK_DIR) -> None:
    """JSONL, append-only: a walk's truth survives the process that saw it."""
    base = os.path.expanduser(base_dir)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, _OBS_FILE), "a") as fh:
        for o in obs:
            fh.write(json.dumps(o.to_dict()) + "\n")
    with open(os.path.join(base, _FAIL_FILE), "a") as fh:
        for f in fails:
            fh.write(json.dumps(f.to_dict()) + "\n")


def _load_jsonl(path: str) -> List[dict]:
    try:
        with open(path) as fh:
            return [json.loads(l) for l in fh if l.strip()]
    except OSError:
        return []


def load_walk_observations(base_dir: str = _WALK_DIR) -> List[LinkObservation]:
    base = os.path.expanduser(base_dir)
    return [LinkObservation.from_dict(d)
            for d in _load_jsonl(os.path.join(base, _OBS_FILE))]


def load_walk_failures(base_dir: str = _WALK_DIR) -> List[LinkFailure]:
    base = os.path.expanduser(base_dir)
    return [LinkFailure.from_dict(d)
            for d in _load_jsonl(os.path.join(base, _FAIL_FILE))]
