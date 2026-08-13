"""SYNAPSE phase 1 — a memory of what every heard link actually looked like.

The mesh already produces link evidence in three places and then forgets it:
the serial splitter records the RSSI/SNR of every packet the medic's own radio
hears (monitor.serial_splitter); a v2 health beacon carries the sending node's
own last-heard link quality (monitor.health_beacon); and the registry knows
where the nodes involved stand and what boards they run
(monitor.registry). This module keeps that evidence: one
:class:`LinkObservation` per sighting, persisted crash-safe, pruned to the
same retention window as beacon history.

Honesty rules, in the repo's standing voice:

* **Absence is a value.** A field the mesh never reported is ``None`` — never a
  defaulted number. A beacon that reports link quality without naming the far
  end is recorded with ``heard_from=None``, because the beacon genuinely does
  not say.
* **Positions carry provenance.** Other people's nodes publish deliberately
  fuzzed pins (~800 m, monitor.geo.FUZZ_RADIUS_M); a distance computed through
  a fuzzed end is not the same fact as one between two birth certificates.
  Every observation says, per end: ``exact`` (birth-certificate coordinates or
  the medic's own GPS fix), ``fuzzed`` (a published pin), or ``unknown``.
  Nothing in the repo ingests other people's fuzzed pins yet, so today the
  stores only ever hold ``exact`` and ``unknown`` — the ``fuzzed`` value exists
  so that when discovery grows that ingest, the store does not have to lie.
* **Old evidence is not this packet's evidence.** Splitter stat frames are
  taken only when the packet timestamp is fresh; a reading two minutes old is
  not cited against the announce that just arrived (dmesg lesson, generalised).

Phase 1 stops at remembering. No range learning, no graph analysis — the
placement engine (monitor.placement) already computes an OBSERVED reach from
the live topology, and phase 2's learned range model must reconcile with it
rather than grow a second spine.

Distance reuses the one public haversine (monitor.movement.haversine_m) —
the repo already carries five private copies; this module adds none.
Pure data + arithmetic; no third-party imports.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Tuple

from monitor.atomic_json import write_json
from monitor.history import RETENTION_S
from monitor.movement import haversine_m
from monitor.topology import MEDIC_ID

__all__ = [
    "POSITION_EXACT", "POSITION_FUZZED", "POSITION_UNKNOWN",
    "LinkObservation", "LinkObservationStore", "STORE_PATH",
    "link_distance_km",
    "observe_medic_heard", "observe_from_beacon", "observe_between",
    "HardwareProfile", "HARDWARE_PROFILES", "profile_for",
    "assumed_link_range_km",
    "DEFAULT_HOP_RANGE_KM", "MAX_HOPS_TO_PROP", "MIN_RELIABILITY",
    "FADE_MARGIN_DB", "PROP_SPACING_KM",
]

# ---- position provenance -----------------------------------------------------

#: The coordinates are the real ones: a birth certificate, or the medic's own
#: GPS fix at the moment of the observation.
POSITION_EXACT = "exact"
#: The coordinates are a published pin, deliberately wrong by up to
#: ~monitor.geo.FUZZ_RADIUS_M. A distance through this end inherits that error.
POSITION_FUZZED = "fuzzed"
#: No coordinates at all. Distinct from fuzzed: "roughly there" and "no idea"
#: must never collapse into one another.
POSITION_UNKNOWN = "unknown"

#: Where the link store lives, beside the other medic records.
STORE_PATH = "~/.reticulum-node-medic/synapse_links.json"

#: A splitter stat frame is only THIS packet's signal if it arrived about when
#: the packet did. Older frames belong to some earlier packet and are not cited.
MAX_PACKET_AGE_S = 5.0


def link_distance_km(lat1: Optional[float], lon1: Optional[float],
                     lat2: Optional[float], lon2: Optional[float]
                     ) -> Optional[float]:
    """Great-circle distance in km, or ``None`` when any coordinate is absent.
    Absence stays absent — never zero."""
    if None in (lat1, lon1, lat2, lon2):
        return None
    return haversine_m(lat1, lon1, lat2, lon2) / 1000.0


# ---- the record --------------------------------------------------------------

@dataclass
class LinkObservation:
    """One sighting of one link, as reported — nothing inferred.

    Direction matters: RSSI/SNR are what ``heard_by`` measured at its own
    antenna. ``heard_from`` is ``None`` when the report did not name the far
    end (a health beacon says "my last-heard link was −97 dBm" without saying
    from whom). Every Optional field stays ``None`` until something actually
    reports it."""

    heard_by: str                       # who measured (MEDIC_ID or a dst hash)
    heard_from: Optional[str]           # far end; None = the report did not say
    observed_at: float                  # epoch seconds
    rssi_dbm: Optional[float] = None    # as heard at heard_by's antenna
    snr_db: Optional[float] = None
    distance_km: Optional[float] = None  # only when both ends have coordinates
    heard_by_position: str = POSITION_UNKNOWN
    heard_from_position: str = POSITION_UNKNOWN
    heard_by_board: Optional[str] = None    # e.g. "Heltec32 V4", where reported
    heard_from_board: Optional[str] = None
    heard_by_antenna_m: Optional[float] = None    # antenna height, where known
    heard_from_antenna_m: Optional[float] = None
    source: str = ""                    # "splitter" | "beacon" | "registry" | …

    def pair(self) -> Optional[Tuple[str, str]]:
        """The link's order-free key, or ``None`` while the far end is unnamed."""
        if self.heard_from is None:
            return None
        return tuple(sorted((self.heard_by, self.heard_from)))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LinkObservation":
        """Rebuild from JSON. A file written before a field existed carries no
        answer for it, and no answer is the field's honest default."""
        return cls(
            heard_by=data["heard_by"],
            heard_from=data.get("heard_from"),
            observed_at=data["observed_at"],
            rssi_dbm=data.get("rssi_dbm"),
            snr_db=data.get("snr_db"),
            distance_km=data.get("distance_km"),
            heard_by_position=data.get("heard_by_position", POSITION_UNKNOWN),
            heard_from_position=data.get("heard_from_position",
                                         POSITION_UNKNOWN),
            heard_by_board=data.get("heard_by_board"),
            heard_from_board=data.get("heard_from_board"),
            heard_by_antenna_m=data.get("heard_by_antenna_m"),
            heard_from_antenna_m=data.get("heard_from_antenna_m"),
            source=data.get("source", ""),
        )


# ---- the store ---------------------------------------------------------------

@dataclass
class LinkObservationStore:
    """The medic's memory of link sightings, pruned to the same retention
    window as beacon history and persisted through the crash-safe writer."""

    observations: List[LinkObservation] = field(default_factory=list)
    retention_s: float = RETENTION_S

    def add(self, obs: Optional[LinkObservation]
            ) -> Optional[LinkObservation]:
        """Keep an observation (and prune). ``None`` passes through untouched,
        so ``store.add(observe_from_beacon(...))`` needs no guard at the
        call site — a beacon with nothing to say adds nothing."""
        if obs is None:
            return None
        self.observations.append(obs)
        self.prune(obs.observed_at)
        return obs

    def prune(self, now: float) -> None:
        cutoff = now - self.retention_s
        self.observations = [o for o in self.observations
                             if o.observed_at >= cutoff]

    def for_pair(self, a: str, b: str) -> List[LinkObservation]:
        """Every sighting of the a↔b link, either direction, oldest first."""
        key = tuple(sorted((a, b)))
        return sorted((o for o in self.observations if o.pair() == key),
                      key=lambda o: o.observed_at)

    def latest(self, a: str, b: str) -> Optional[LinkObservation]:
        hits = self.for_pair(a, b)
        return hits[-1] if hits else None

    def to_dict(self) -> dict:
        return {"retention_s": self.retention_s,
                "observations": [o.to_dict() for o in self.observations]}

    @classmethod
    def from_dict(cls, data: dict) -> "LinkObservationStore":
        store = cls(retention_s=data.get("retention_s", RETENTION_S))
        store.observations = [LinkObservation.from_dict(d)
                              for d in data.get("observations", [])]
        return store

    def save(self, path: str = STORE_PATH) -> bool:
        """Atomically persist (fsync + rename via monitor.atomic_json) — power
        can vanish mid-write on this hardware. Returns False on failure,
        never raises."""
        return write_json(path, self.to_dict())

    @classmethod
    def load(cls, path: str = STORE_PATH) -> "LinkObservationStore":
        """Load a saved store, or start clean if the file is missing or
        unreadable — a corrupt memory is an empty memory, not a crash."""
        try:
            with open(os.path.expanduser(path)) as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return cls()


# ---- ingest helpers (fed from what exists today) -----------------------------

def _record_facts(rec) -> Tuple[Optional[float], Optional[float], str,
                                Optional[str]]:
    """(lat, lon, position provenance, board) for a registry record, or the
    honest nothing for a record the registry does not hold. Registry lat/lon
    are birth-certificate coordinates, so a located record is *exact*; the
    registry holds no fuzzed pins today (see the module docstring)."""
    if rec is None:
        return None, None, POSITION_UNKNOWN, None
    lat, lon = rec.lat, rec.lon
    position = POSITION_EXACT if rec.has_location() else POSITION_UNKNOWN
    board = rec.latest_beacon.board_label if rec.latest_beacon else None
    return lat, lon, position, board


def observe_medic_heard(dst_hash: str, registry, splitter_state: Optional[dict],
                        now: float,
                        max_packet_age_s: float = MAX_PACKET_AGE_S,
                        medic_board: Optional[str] = None,
                        medic_antenna_m: Optional[float] = None
                        ) -> LinkObservation:
    """The medic's radio just heard *dst_hash* (an announce or beacon arrived).

    Signal comes from the serial splitter's state dict
    (:meth:`monitor.serial_splitter.KissGpsSplitter.state`) — but only when its
    per-packet stats are fresh (within *max_packet_age_s* of *now*). A stale
    stat frame belongs to an earlier packet and is left uncited. The medic's
    own position comes from the same state (its live GPS fix = exact); the far
    node's from the registry. *medic_board* / *medic_antenna_m* are recorded
    only if the caller states them — this module does not guess the medic's
    hardware."""
    state = splitter_state or {}
    rssi = snr = None
    heard_at = state.get("packet_heard_at")
    if heard_at is not None and (now - heard_at) <= max_packet_age_s:
        rssi = state.get("last_rssi")
        snr = state.get("last_snr")

    m_lat = state.get("lat") if state.get("has_fix") else None
    m_lon = state.get("lng") if state.get("has_fix") else None
    m_position = POSITION_EXACT if (m_lat is not None and m_lon is not None) \
        else POSITION_UNKNOWN

    n_lat, n_lon, n_position, n_board = _record_facts(
        registry.get(dst_hash) if registry else None)

    return LinkObservation(
        heard_by=MEDIC_ID, heard_from=dst_hash, observed_at=now,
        rssi_dbm=rssi, snr_db=snr,
        distance_km=link_distance_km(m_lat, m_lon, n_lat, n_lon),
        heard_by_position=m_position, heard_from_position=n_position,
        heard_by_board=medic_board, heard_from_board=n_board,
        heard_by_antenna_m=medic_antenna_m,
        source="splitter")


def observe_from_beacon(dst_hash: str, beacon, now: float,
                        registry=None) -> Optional[LinkObservation]:
    """A node's own link report, from its v2 health beacon.

    The beacon carries the node's last-heard LoRa RSSI/SNR *without naming the
    peer*, so the far end is honestly ``None`` — a one-ended observation, still
    worth keeping (it is the only link telemetry a remote node ever volunteers).
    Returns ``None`` when the beacon carries no link telemetry at all: nothing
    reported, nothing recorded."""
    if not beacon.has_link_telemetry:
        return None
    lat, lon, position, _ = _record_facts(
        registry.get(dst_hash) if registry else None)
    del lat, lon                       # one end only — no distance to compute
    return LinkObservation(
        heard_by=dst_hash, heard_from=None, observed_at=now,
        rssi_dbm=beacon.lora_rssi_dbm, snr_db=beacon.lora_snr_db,
        heard_by_position=position,
        heard_by_board=beacon.board_label,
        source="beacon")


def observe_between(a_hash: str, b_hash: str, registry, now: float,
                    rssi_dbm: Optional[float] = None,
                    snr_db: Optional[float] = None,
                    source: str = "registry") -> LinkObservation:
    """An observation of a named pair, enriched from the registry.

    For link evidence that arrives already attributed — a path-table row
    proving a↔b relays, a triage session measuring a link — this fills in
    what the registry knows about both ends (positions with provenance,
    boards, distance where computable). Signal is recorded only if the caller
    measured it; this helper never invents one."""
    a_lat, a_lon, a_position, a_board = _record_facts(registry.get(a_hash))
    b_lat, b_lon, b_position, b_board = _record_facts(registry.get(b_hash))
    return LinkObservation(
        heard_by=a_hash, heard_from=b_hash, observed_at=now,
        rssi_dbm=rssi_dbm, snr_db=snr_db,
        distance_km=link_distance_km(a_lat, a_lon, b_lat, b_lon),
        heard_by_position=a_position, heard_from_position=b_position,
        heard_by_board=a_board, heard_from_board=b_board,
        source=source)


# ---- hardware profiles: named assumptions, waiting to be overruled -----------
#
# Everything below is engineering judgement, not physics. These numbers exist
# so planning can start before the mesh has taught the medic anything; the
# phase-2 learned range model overrides them the moment enough LinkObservations
# exist to measure instead of assume. They sit beside two OTHER standing
# defaults that must be reconciled, not multiplied: monitor.placement already
# self-calibrates an observed reach from live links (falling back to 3 km gap /
# 1.2 km extend for a brand-new mesh), and TODO.md's standing constraint is
# "calibrate against *this* mesh, never a datasheet".

#: The operator's starting assumption, stated 2026-08-12: "a rooftop node
#: reaches 5 km". Engineering judgement, not physics — real reach depends on
#: terrain, antenna, and noise; the learned model replaces this per hardware
#: pair once observations suffice.
DEFAULT_HOP_RANGE_KM = 5.0

#: Planning ceiling on hops between any node and its nearest propagation node.
#: Engineering judgement, not physics (each LoRa hop adds latency and a chance
#: to drop); the learned model refines what a hop is worth.
MAX_HOPS_TO_PROP = 10

#: The fraction of observed traffic a link must deliver before planning treats
#: it as dependable. Engineering judgement, not physics — a starting bar until
#: learned per-link reliability replaces it.
MIN_RELIABILITY = 0.80

#: Signal margin (dB) demanded above the decode floor before a link counts as
#: solid rather than a fluke. Engineering judgement, not physics — fading is
#: weather, foliage and traffic; the learned model measures it per link.
FADE_MARGIN_DB = 10

#: Target spacing between LXMF propagation nodes. Engineering judgement, not
#: physics — a mesh-shape aspiration, revisited once the learned model knows
#: real multi-hop reach.
PROP_SPACING_KM = 100


@dataclass(frozen=True)
class HardwareProfile:
    """A board's default range assumption. Tunable, named, and honest about
    being a guess — every profile is engineering judgement until the learned
    model has observations for that hardware."""
    board: str
    assumed_range_km: float = DEFAULT_HOP_RANGE_KM
    note: str = ""


#: Per-board starting assumptions, keyed by the board names the repo already
#: speaks (monitor.health_beacon.BOARD_IDS / node_profile.NodeHardware). All
#: start at the operator's rooftop figure — nothing here has earned a different
#: number yet; when field evidence says otherwise, tune the profile (or better,
#: let phase 2 learn it).
HARDWARE_PROFILES = {
    p.board: p for p in (
        HardwareProfile("Heltec32 V3",
                        note="Operator default — no measured reach yet."),
        HardwareProfile("Heltec32 V4",
                        note="Operator default — no measured reach yet."),
        HardwareProfile("Heltec Wireless Tracker",
                        note="Operator default — no measured reach yet."),
        HardwareProfile("RPi propagation",
                        note="Radio is whatever RNode the Pi carries — "
                             "operator default until measured."),
    )
}


def profile_for(board: Optional[str]) -> HardwareProfile:
    """The profile for *board*, or the mesh-wide default for hardware nobody
    has written an assumption for (including ``None`` — board unreported)."""
    if board in HARDWARE_PROFILES:
        return HARDWARE_PROFILES[board]
    return HardwareProfile(
        board=board or "unknown",
        note="No profile for this board — the mesh-wide default assumption.")


def assumed_link_range_km(a_board: Optional[str],
                          b_board: Optional[str]) -> float:
    """The starting range assumption for a link between two boards: a link is
    no longer than its weaker end assumes. Still judgement, not physics."""
    return min(profile_for(a_board).assumed_range_km,
               profile_for(b_board).assumed_range_km)
