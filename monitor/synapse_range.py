"""SYNAPSE phase 2 — learning how far a link reaches, without lying about it.

The phase-1 store (monitor.synapse_links) remembers what every heard link
looked like. This module turns that memory into ONE range number for
placement — and everything around the number exists to keep it honest:

* **Thin data is the default, labelled.** Below MIN_DISTINCT_LINKS distinct
  links (pairs, never repeat observations of one pair — a chatty link is one
  link), the estimate IS the phase-1 assumption, said out loud.
* **Survivorship is a first-class caveat.** A store fed only by links that
  worked measures nothing about where links fail. Until negative evidence
  arrives (the boundary walk, docs/SYNAPSE_DECISIONS.md), confidence stays
  low and the estimate says why.
* **Losses are believed, not averaged away.** A measured failure closer than
  the estimate pulls confidence down and is surfaced with its date — but it
  never silently contracts the number, because contracting on every loss and
  re-judging the remaining losses against the smaller number is a ratchet
  to zero. The operator sees the conflict; more data resolves it.
* **Fuzzed positions are handled by rule.** An endpoint that only ever
  published a deliberately-wrong pin (~monitor.geo.FUZZ_RADIUS_M) is excluded
  from range fitting when the link is short enough for the fuzz to dominate,
  and admitted with inflated variance when the link is long enough that it
  cannot.

Confidence here is engineering judgement, not statistics with a pedigree —
sample count, spread and the survivorship state, mapped to four plain words.

This estimator is meant to become the single range spine: placement's
internal fallbacks (monitor.placement.observed_reach_km and its 3 km / 1.2 km
brand-new-mesh constants) answer the same question and phase 3/4 wires
``estimate_range`` in beneath ``suggest()``. placement.py is deliberately not
touched in this phase; its REACH_PERCENTILE = 90 stays the SCAN display
halo's number, which answers "what has this mesh ever spanned", not "what
should we plan on".

Pure arithmetic over phase-1 records; no third-party imports.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from monitor.geo import FUZZ_RADIUS_M
from monitor.synapse_links import (
    DEFAULT_HOP_RANGE_KM,
    FADE_MARGIN_DB,
    LinkObservation,
    LinkObservationStore,
    POSITION_FUZZED,
)

__all__ = [
    "PLACEMENT_PERCENTILE", "MIN_DISTINCT_LINKS", "PROJECTION_CAP",
    "RSSI_FLOOR_DBM", "CO_LOCATED_KM", "FUZZ_EXCLUSION_FACTOR",
    "LinkFailure", "RangeEstimate",
    "nearest_rank", "estimate_range", "walk_failures",
]

#: The percentile of observed working-link distances that placement should
#: plan on. Provenance: operator, 2026-08-13 (docs/SYNAPSE_DECISIONS.md),
#: choosing between the display halo's optimistic 90th and a draft's
#: conservative 25th. The halo keeps its 90th
#: (monitor.placement.REACH_PERCENTILE) — two questions, two numbers, both
#: named. Engineering judgement, not physics.
PLACEMENT_PERCENTILE = 70

#: Distinct LINKS (node pairs), not observations, required before the learned
#: estimate overrides DEFAULT_HOP_RANGE_KM. Five observations of one link are
#: one link. Engineering judgement — a floor against deciding the mesh's
#: reach from one lucky pair.
MIN_DISTINCT_LINKS = 5

#: The signal projection may extrapolate at most this far past the farthest
#: MEASURED link. Beyond it the fitted line is talking about ground no packet
#: has crossed. Engineering judgement, not physics.
PROJECTION_CAP = 1.5

#: Where decode gives out, roughly, for these radios (dBm at the antenna).
#: Engineering judgement, not physics — the true floor moves with spreading
#: factor, bandwidth and noise; this only anchors the advisory projection.
RSSI_FLOOR_DBM = -120

#: Links shorter than this prove nothing about range (placement.py applies the
#: same rule) and would feed log10(0) if projected. Co-located pairs are
#: excluded from fitting entirely.
CO_LOCATED_KM = 0.01

#: A fuzzed-endpoint observation is excluded from range fitting when its
#: distance is under this multiple of the combined fuzz radius — short links
#: through a deliberately-wrong pin are mostly fuzz. Operator rule, 2026-08-13.
FUZZ_EXCLUSION_FACTOR = 2.0

#: Spread bar for "high" confidence: relative spread (stdev / median link
#: distance) at or under this. Engineering judgement.
_HIGH_CONFIDENCE_SPREAD = 0.5
#: Distinct links required for "high" confidence. Engineering judgement.
_HIGH_CONFIDENCE_LINKS = 10


def nearest_rank(values: Iterable[float], percentile: float) -> float:
    """The nearest-rank percentile: the ceil(p/100 * n)-th smallest value.

    Chosen over interpolation deliberately: nearest-rank always returns a
    distance some link ACTUALLY achieved, where interpolating between two
    observed distances manufactures a distance nobody ever measured — the
    same honesty rule as everywhere else in the medic. Pinned by test at
    exactly n=5, where the conventions disagree."""
    vs = sorted(values)
    if not vs:
        raise ValueError("nearest_rank of no values")
    k = math.ceil(percentile / 100.0 * len(vs))
    return vs[min(max(k, 1), len(vs)) - 1]


@dataclass
class LinkFailure:
    """A measured link FAILURE at a known distance — the negative evidence
    only a boundary walk produces (a lost ping at a known spot). The shape
    extends monitor.first_link's walk samples; ``walk_failures`` adapts them."""
    distance_km: float
    observed_at: float                 # epoch seconds
    lat: Optional[float] = None
    lon: Optional[float] = None
    snr_db: Optional[float] = None     # last heard before the loss, if any
    note: str = ""
    #: Which node's boundary this loss belongs to — the SAME key a walk's
    #: LinkObservations are banked under (monitor.boundary_walk
    #: .BoundaryWalkSession.evidence: ``evidence_key or node_key``, itself
    #: monitor.walk_anchor.anchor_key's convention: device_id, else
    #: identity_hash, else dst_hash). Added 2026-09-24 for the boundary-ring
    #: feature (monitor.boundary_shape), which needs to know WHOSE edge each
    #: loss draws — estimate_range never needed one, so every failure banked
    #: before this date reads ``None`` here: real evidence, un-attributable
    #: to one node's ring, same honesty rule as every other absent field.
    node_key: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "LinkFailure":
        return cls(distance_km=data["distance_km"],
                   observed_at=data["observed_at"],
                   lat=data.get("lat"), lon=data.get("lon"),
                   snr_db=data.get("snr_db"), note=data.get("note", ""),
                   node_key=data.get("node_key"))


def walk_failures(attempts: Iterable[dict], observed_at: float,
                  note: str = "boundary walk",
                  node_key: Optional[str] = None) -> List[LinkFailure]:
    """Adapt a walk protocol's samples (first_link / boundary walk shape:
    ``{lat, lon, km, connected, snr_db}``) into failures. Only the samples
    that did NOT connect are failures — a weak connection is still a link.
    *node_key* is stamped onto every failure produced (default None for a
    caller that does not know, or does not need, whose node this was —
    estimate_range pools failures mesh-wide and never asked)."""
    return [LinkFailure(distance_km=a["km"], observed_at=observed_at,
                        lat=a.get("lat"), lon=a.get("lon"),
                        snr_db=a.get("snr_db"), note=note, node_key=node_key)
            for a in attempts if not a.get("connected")]


@dataclass
class RangeEstimate:
    """One range number, wearing everything the operator needs to distrust it
    correctly. ``range_km`` is what placement should plan on; every other
    field says where it came from and how far to trust it."""
    range_km: float
    source: str                        # "default" | "measured"
    percentile: float
    distinct_links: int                # distinct pairs behind the estimate
    observation_count: int             # observations those pairs contributed
    excluded_fuzzed: int               # observations dropped by the fuzz rule
    variance_km2: Optional[float]      # spread of link distances (+ fuzz), 2+ links
    survivorship: bool                 # True = success-only data, tail unbounded
    confidence: str                    # "none" | "low" | "medium" | "high"
    contradictions: List[str] = field(default_factory=list)   # dated loss lines
    projected_reach_km: Optional[float] = None   # advisory extrapolation, capped
    cautions: List[str] = field(default_factory=list)


def _combined_fuzz_km(obs: LinkObservation) -> float:
    ends = (obs.heard_by_position, obs.heard_from_position)
    return (FUZZ_RADIUS_M / 1000.0) * sum(1 for p in ends
                                          if p == POSITION_FUZZED)


def _partition(store: LinkObservationStore
               ) -> Tuple[List[LinkObservation], int]:
    """(observations usable for range fitting, count excluded by the fuzz
    rule). Usable = named pair, real distance, not co-located, and not a
    short link seen through a fuzzed pin."""
    usable: List[LinkObservation] = []
    excluded_fuzzed = 0
    for obs in store.observations:
        if obs.pair() is None or obs.distance_km is None:
            continue
        if obs.distance_km <= CO_LOCATED_KM:
            continue
        fuzz = _combined_fuzz_km(obs)
        if fuzz > 0 and obs.distance_km < FUZZ_EXCLUSION_FACTOR * fuzz:
            excluded_fuzzed += 1
            continue
        usable.append(obs)
    return usable, excluded_fuzzed


def _per_link(usable: List[LinkObservation]
              ) -> Tuple[List[float], List[float]]:
    """Collapse observations to one voice per LINK: (median distance per pair,
    that pair's worst-case fuzz radius). Pseudo-replication guard — fifty
    sightings of one link must not outvote four quiet links."""
    by_pair: Dict[tuple, List[LinkObservation]] = {}
    for obs in usable:
        by_pair.setdefault(obs.pair(), []).append(obs)
    distances, fuzzes = [], []
    for group in by_pair.values():
        distances.append(statistics.median(o.distance_km for o in group))
        fuzzes.append(max(_combined_fuzz_km(o) for o in group))
    return distances, fuzzes


def _project_reach(usable: List[LinkObservation]
                   ) -> Tuple[Optional[float], List[str]]:
    """Advisory reach from a log-distance fit of measured RSSI — labelled as
    the extrapolation it is, and capped at PROJECTION_CAP x the farthest
    measured link. Never sets the planning range. Refuses to fit nonsense
    (signal rising with distance) and cannot see log10(0): co-located
    samples were already excluded upstream."""
    samples = [(o.distance_km, o.rssi_dbm) for o in usable
               if o.rssi_dbm is not None]
    if len(samples) < 3 or len({d for d, _ in samples}) < 2:
        return None, []
    xs = [math.log10(d) for d, _ in samples]
    ys = [r for _, r in samples]
    xm, ym = statistics.fmean(xs), statistics.fmean(ys)
    varx = sum((x - xm) ** 2 for x in xs)
    slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / varx
    if slope >= 0:
        return None, ["Signal readings do not fall with distance in this "
                      "data — refusing to project a reach from them."]
    intercept = ym - slope * xm
    target = RSSI_FLOOR_DBM + FADE_MARGIN_DB
    reach = 10 ** ((target - intercept) / slope)
    farthest = max(d for d, _ in samples)
    cautions = []
    cap = PROJECTION_CAP * farthest
    if reach > cap:
        reach = cap
        cautions.append(
            f"Signal projection capped at {PROJECTION_CAP:g}x the farthest "
            f"measured link ({farthest:g} km) — past that is ground no "
            "packet has crossed.")
    cautions.append(
        f"Projected reach {reach:.1f} km is an extrapolation from signal "
        "readings, not a measurement — plan on the measured range.")
    return reach, cautions


def _date(epoch: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def estimate_range(store: LinkObservationStore,
                   failures: Iterable[LinkFailure] = (),
                   percentile: float = PLACEMENT_PERCENTILE,
                   default_km: float = DEFAULT_HOP_RANGE_KM) -> RangeEstimate:
    """The one range number, from everything the mesh has actually shown.

    Positive evidence comes from the phase-1 store; negative evidence
    (*failures*) from walk protocols. *default_km* is what stands while the
    data is thin — pass ``assumed_link_range_km(...)`` to make it the
    hardware pair's assumption instead of the mesh-wide one."""
    failures = list(failures)
    usable, excluded_fuzzed = _partition(store)
    link_km, link_fuzz = _per_link(usable)
    distinct = len(link_km)

    cautions: List[str] = []
    variance = None
    if distinct >= 2:
        variance = statistics.pvariance(link_km) \
            + statistics.fmean(f ** 2 for f in link_fuzz)

    if distinct >= MIN_DISTINCT_LINKS:
        range_km = nearest_rank(link_km, percentile)
        source = "measured"
        if statistics.pvariance(link_km) == 0:
            cautions.append(
                f"All {distinct} links sit at about {link_km[0]:g} km — the "
                "spread is zero, and sameness is not certainty about nearer "
                "or farther.")
    else:
        range_km = default_km
        source = "default"
        cautions.append(
            f"Only {distinct} distinct links measured — the {default_km:g} km "
            f"assumption stands until {MIN_DISTINCT_LINKS} (engineering "
            "judgement, not evidence).")

    survivorship = not failures
    if survivorship and usable:
        cautions.append(
            "Every distance here comes from a link that worked; nothing "
            "measured where links fail. A boundary walk supplies the "
            "missing half.")

    contradictions = [
        f"measured a loss at {f.distance_km:g} km on {_date(f.observed_at)} "
        f"— inside the {range_km:g} km estimate; trust the loss"
        for f in failures if f.distance_km < range_km]

    if distinct < MIN_DISTINCT_LINKS:
        confidence = "none"
    elif contradictions or survivorship:
        confidence = "low"
    else:
        spread_ok = (variance is not None
                     and math.sqrt(max(0.0, variance))
                     <= _HIGH_CONFIDENCE_SPREAD * statistics.median(link_km))
        confidence = "high" if (distinct >= _HIGH_CONFIDENCE_LINKS
                                and spread_ok) else "medium"

    projected, proj_cautions = _project_reach(usable)
    cautions.extend(proj_cautions)

    return RangeEstimate(
        range_km=range_km, source=source, percentile=percentile,
        distinct_links=distinct, observation_count=len(usable),
        excluded_fuzzed=excluded_fuzzed, variance_km2=variance,
        survivorship=survivorship, confidence=confidence,
        contradictions=contradictions, projected_reach_km=projected,
        cautions=cautions)
