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
from typing import Iterable, List, Optional, Tuple

from monitor.synapse_links import LinkObservation, POSITION_EXACT
from monitor.synapse_range import LinkFailure, walk_failures, CO_LOCATED_KM

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
    #: What the evidence is banked AS (``LinkObservation.heard_from``).
    #: ``node_key`` is the mesh destination that is PINGED; a device
    #: announces several, and the two doors into a walk pick different
    #: ones — so two walks against one T114 counted as two "distinct
    #: links" in estimate_range (review, 2026-09-23). The screen passes
    #: the device key the anchor is locked under (monitor.walk_anchor
    #: .anchor_key); None keeps the old behaviour of banking under the
    #: pinged destination.
    evidence_key: Optional[str] = None
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

    def ping_sent_at(self) -> Optional[float]:
        """When the in-flight (or last) ping went out — the moment a reply's
        signal must postdate to be the reply's."""
        return self._last_ping_at

    # -- results ------------------------------------------------------------

    def _anchor(self) -> Optional[Tuple[float, float]]:
        if self.node_lat is not None and self.node_lon is not None:
            return (self.node_lat, self.node_lon)
        return None

    def ping_result(self, now: float, ok: bool,
                    snr_db: Optional[float] = None,
                    gps: Optional[Tuple[float, float]] = None,
                    rssi_dbm: Optional[float] = None,
                    hops: Optional[int] = None,
                    direct: Optional[bool] = None) -> dict:
        """One outcome in. GPS may be None (no fix): the outcome still counts
        for the story, but carries no distance and feeds no range evidence —
        a measurement without a place measures nothing about reach.
        *rssi_dbm*/*snr_db* are the reply as heard by the medic's own radio
        (2026-09-21 — the first walk had none, and distance-to-silence alone
        cannot tell a slope from a cliff). *direct* False means the path
        came via a relay or the LAN: told in the story, not banked as this
        radio's reach."""
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
                  "connected": bool(ok), "snr_db": snr_db,
                  "rssi_dbm": rssi_dbm, "hops": hops, "direct": direct}
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

    def counts(self) -> dict:
        """The numbers the screen paints, in one place — so the banner, the
        hint and the summary cannot disagree. ``unplaced`` is the pings taken
        without a live fix (2026-09-21: the operator's first walk went back indoors; those pings counted for the story but could not
        be placed, and the banner used to count them as if they had been)."""
        hits = sum(1 for s in self.samples if s["connected"])
        last_hit = next((s for s in reversed(self.samples) if s["connected"]),
                        None)
        return {"hits": hits,
                "misses": len(self.samples) - hits,
                "unplaced": sum(1 for s in self.samples if s["km"] is None),
                "relayed": sum(1 for s in self.samples
                               if s["connected"] and s.get("direct") is False),
                "last_rssi": last_hit.get("rssi_dbm") if last_hit else None,
                "last_snr": last_hit.get("snr_db") if last_hit else None,
                "max_km": self.max_linked_km}

    def last_unplaced(self) -> bool:
        """Was the most recent ping taken without a live fix? The hint reads
        this: a loss it cannot place is not "the edge of its reach"."""
        return bool(self.samples) and self.samples[-1]["km"] is None

    def summary(self) -> str:
        c = self.counts()
        tail = (f" {c['unplaced']} had no satellite fix and were not placed."
                if c["unplaced"] else "")
        return (f"{c['hits']} pings answered, {c['misses']} silent; furthest "
                f"placed link {c['max_km']:.2f} km from {self.node_name}."
                + tail)

    # -- evidence ------------------------------------------------------------

    def _confirmed_sample_indices(self) -> set:
        """Indices into ``self.samples`` that belong to a run of >=
        LOST_AFTER_MISSES CONSECUTIVE misses, in the walk's own sample
        sequence, by time — the exact same run the "MESH CONNECTION LOST"
        banner is already waiting for (ping_result's ``self._misses``
        counter, recomputed here from the finished sample list rather than
        re-run live). A run counts EVERY miss in it, including the ones
        before the threshold was crossed — once a run proves itself a real
        loss and not one dropped frame, the whole run is the same evidence.
        A miss with no GPS fix still counts toward the run length (the
        banner does not care whether a miss was placeable), it just never
        becomes a LinkFailure of its own (see ``evidence``)."""
        confirmed: set = set()
        run: List[int] = []
        for i, s in enumerate(self.samples):
            if s["connected"]:
                if len(run) >= LOST_AFTER_MISSES:
                    confirmed.update(run)
                run = []
            else:
                run.append(i)
        if len(run) >= LOST_AFTER_MISSES:
            confirmed.update(run)
        return confirmed

    def evidence(self, medic_id: str = "MEDIC"
                 ) -> Tuple[List[LinkObservation], List[LinkFailure]]:
        """Everything the walk proved, in the range model's two currencies.
        Only located samples qualify — see ping_result."""
        # A relayed answer (direct False) is the mesh's reach, not this
        # radio's — never a link observation between medic and node.
        obs = [LinkObservation(
                   heard_by=medic_id,
                   heard_from=self.evidence_key or self.node_key,
                   observed_at=s["t"], snr_db=s["snr_db"],
                   rssi_dbm=s.get("rssi_dbm"),
                   distance_km=s["km"],
                   heard_by_position=POSITION_EXACT,
                   heard_from_position=POSITION_EXACT,
                   source="boundary_walk")
               for s in self.samples if s["connected"] and s["km"] is not None
               and s.get("direct") is not False]
        # A miss AT the node is not a loss. The first ping fires the moment
        # the walk starts, standing at the anchor, and one LoRa frame can
        # die of anything — banked, it read "measured a loss at 0 km, trust
        # the loss" (agents' audit, 2026-09-21). Observations already drop
        # co-located samples (synapse_range CO_LOCATED_KM); same rule here.
        # Each loss keeps its own time, not the walk's last one.
        # node_key mirrors the obs line above exactly (evidence_key or
        # node_key) — one convention, both currencies (2026-09-24, added so
        # the boundary-ring feature can tell whose edge a loss draws;
        # LinkFailure carried no node identity before this).
        # confirmed (2026-09-24 review): True only for a loss that belongs
        # to a run of >= LOST_AFTER_MISSES consecutive misses — see
        # ``_confirmed_sample_indices``. monitor.boundary_shape.node_boundary
        # trusts only confirmed losses for a ring's radius; an isolated miss
        # is still banked (the model wants the whole truth) but never draws
        # a line by itself.
        confirmed_idx = self._confirmed_sample_indices()
        fails: List[LinkFailure] = []
        for i, s in enumerate(self.samples):
            if s["km"] is None or s["km"] <= CO_LOCATED_KM:
                continue
            for f in walk_failures([s], observed_at=s["t"],
                                   node_key=self.evidence_key
                                   or self.node_key):
                f.confirmed = i in confirmed_idx
                fails.append(f)
        return obs, fails


# -- the GPS gate: no anchor, no walk ---------------------------------------

#: A receiver with open sky above it finds its first fix inside this long. Past
#: it the gate stops saying "wait" and starts saying "check" — two minutes of
#: nothing is a problem, not patience.
GPS_COLD_START_S = 120.0


def walk_position(fix) -> Optional[Tuple[float, float]]:
    """Where a walk sample is placed — or None, which ping_result treats as
    "counts for the story, not for the evidence". Only a LIVE fix places a
    sample. A coasting fix (flag up, 0 sats — classify_fix's ``held``) is the
    receiver repeating where it last was; the operator's first walk plan
    (2026-09-21) goes from the node straight back indoors, and
    every indoor ping would land on the anchor — a silent one there is a
    0 km loss the range model is built to trust. The gate's rule, applied to
    every ping."""
    from monitor.geo import classify_fix, valid_position
    if classify_fix(fix) != "live":
        return None
    # The gate's other rule too (2026-09-23): a live flag on a position
    # that is not on Earth (NaN, (0,0)) places nothing. Unplaced, not
    # banked — and never a NaN in walk_observations.jsonl.
    if not valid_position(fix.lat, fix.lon):
        return None
    return (fix.lat, fix.lon)


def signal_for_answer(state: Optional[dict], sent_at: Optional[float]
                      ) -> Tuple[Optional[float], Optional[float]]:
    """(rssi_dbm, snr_db) of the reply as the medic's own radio heard it —
    the splitter records both per received packet with ``packet_heard_at``.
    Only a packet heard AFTER the ping went out can be the reply; anything
    older is some other node's traffic and is not reported (2026-09-21)."""
    if not state or sent_at is None:
        return (None, None)
    heard = state.get("packet_heard_at")
    if heard is None or heard < sent_at:
        return (None, None)
    return (state.get("last_rssi"), state.get("last_snr"))


def gps_gate(fix, waited_s: float = 0.0) -> dict:
    """May the walk start yet, and what is the screen waiting on?

    Operator, 2026-09-21, with a T114 outdoors: the walk must not begin until the medic has a satellite fix, and
    the start button must not EXIST until then.

    The reason is in ``ping_result`` above. A walk's whole product is evidence
    at a KNOWN DISTANCE, and the distance is measured from the anchor — where
    the operator stood when they pressed start. With no fix every ping records
    ``km: None``, ``evidence`` banks nothing, and the screen sits there
    pinging away looking busy while measuring exactly nothing. That is the
    shape this project calls decoration: a step whose failure is invisible.

    A COASTING fix is refused with the rest, and named separately so the
    screen can say which. ``classify_fix`` calls it ``held`` — a fix flag with
    zero satellites tracked, which is the receiver replaying where it WAS.
    Anchoring a range measurement on a remembered position would put a wrong
    number on every sample in the walk, and a wrong number is worse than none.

    Returns ``{ready, stage, sats, accuracy_m, anchor}`` where *stage* is one
    of ``searching`` | ``slow`` | ``held`` | ``invalid`` | ``ready`` and
    *anchor* is the ``(lat, lon)`` to measure from, present only when ready.
    ``invalid`` (2026-09-23) is a LIVE fix whose position is not on Earth —
    NaN, (0, 0), a latitude of 91 — which the screen names as its own
    state: the sky is fine, the receiver's numbers are not, and the walk
    must not start on them. *accuracy_m* is the fix's own estimate
    (GpsFix.accuracy_m, HDOP-derived, or None), carried so the anchor
    record can say how good the fix it was taken on was.

    Pure, like the rest of this module: the words live in the screen, wrapped
    for translation. This decides, it does not speak.
    """
    from monitor.geo import classify_fix, valid_position
    try:
        level = classify_fix(fix)
    except Exception:                                              # noqa: BLE001
        # The fix reader is a file read on a device. It has handed back None,
        # a stale dict and worse; a malformed one is "no fix", never a crash
        # with the operator standing outside in the weather.
        level = "none"
    sats = getattr(fix, "sats", None)
    if isinstance(sats, bool) or not isinstance(sats, int):
        sats = None
    acc = getattr(fix, "accuracy_m", None)
    if (isinstance(acc, bool) or not isinstance(acc, (int, float))
            or not math.isfinite(acc)):
        acc = None
    base = {"ready": False, "sats": sats, "accuracy_m": acc, "anchor": None}
    if level == "live":
        if not valid_position(getattr(fix, "lat", None),
                              getattr(fix, "lon", None)):
            return {**base, "stage": "invalid"}
        return {**base, "ready": True, "stage": "ready",
                "anchor": (fix.lat, fix.lon)}
    if level == "held":
        return {**base, "stage": "held"}
    return {**base,
            "stage": "slow" if waited_s >= GPS_COLD_START_S else "searching"}


def leave_plan(walk_live: bool, gate_up: bool, has_samples: bool = False
               ) -> dict:
    """What the bottom bar's arrow or Home does to a walk (operator,
    2026-09-23, mid-walk on the roof node: "I just accidentally exited the
    boundary walk halfway through it").

    THE FACT BEFORE THE FIX: ScanScreen defined neither ``handle_back`` nor
    ``handle_home``, so App._with_back switched straight to home. Leaving
    did NOT call end_walk — the session's Clock intervals kept pinging with
    no HUD on the glass and no road back; the walk was only banked when
    the app stopped, or when the next press on a door found a running
    session with samples (begin_walk). The gate's clock likewise kept
    looking at the sky on a screen nobody was on.

    ONE RULE FOR PERSIST (review, 2026-09-23), three cases:

    * a live walk WITH samples — ask; on yes, bank it (``persist`` True,
      ``reason`` "save"): "End the walk and save";
    * a live walk with NO samples — ask; nothing to bank (``persist``
      False, ``reason`` "empty"): "Leave — nothing recorded yet", and no
      green "finished" card for a walk that measured nothing;
    * a gate that is DOING something (the GPS clock running, the pre-check
      probe in flight) — ask; ``persist`` False, ``reason`` "gate": "Leave
      — nothing to save". A refusal card or a finished check panel is NOT
      a gate up: those have their own Close, and the caller just tears them
      down and lets the bar go home.

    Returns ``{ask, persist, then, reason}``; *then* — where the screen goes
    afterwards. SCAN has no inner page, so back and home both end at home.
    Pure: this decides, the screen asks and moves.
    """
    if walk_live:
        if has_samples:
            return {"ask": True, "persist": True, "then": "home",
                    "reason": "save"}
        return {"ask": True, "persist": False, "then": "home",
                "reason": "empty"}
    if gate_up:
        return {"ask": True, "persist": False, "then": "home",
                "reason": "gate"}
    return {"ask": False, "persist": False, "then": "home", "reason": None}


#: How recently a node must have been heard to be worth PROBING. Deliberately
#: generous: since 2026-09-19 the registry only nominates candidates and a
#: live ping decides who is actually offered (live_walk_targets), so this
#: window answers "is this worth a probe?", not "is this alive?". A node
#: quiet for most of a day may be perfectly well and simply between beacons.
WALK_CANDIDATE_MAX_AGE_H = 36.0

#: Never probe more than this many candidates when the picker opens — each
#: probe costs seconds of the operator's time, standing outside.
WALK_PROBE_LIMIT = 8


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
    # ONE PHYSICAL NODE, ONE ENTRY. A node announces on several destinations
    # (identity, health, LXMF) — a T114 sat in the registry under four
    # (2026-09-19). Listing it four times wastes the operator's attention and
    # probing it four times wastes their daylight. Keep the freshest
    # destination per device: it is the one likeliest to answer.
    seen, deduped = set(), []
    for n in out:
        key = (n["name"] or "").strip().lower() or n["dst_hash"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(n)
    return deduped


def answers_now(node: dict, probe) -> Optional[dict]:
    """The node, marked live, if it ANSWERS RIGHT NOW — else ``None``.

    Operator, 2026-09-19: "send out a ping and only offer nodes that are
    currently connected when the user is about to do the walk." The
    project's oldest law wearing new clothes — a remembered sighting is not
    a sighting. *probe* is ``(dst_hash) -> bool``, injected so the rule
    stays pure and the caller can run it off-thread (the picker probes its
    candidates in parallel; the operator is standing outside in the
    weather). A probe that RAISES is a no, never a crash.
    """
    try:
        ok = bool(probe(node["dst_hash"]))
    except Exception:                                              # noqa: BLE001
        ok = False
    return {**node, "answered_now": True} if ok else None


# -- persistence: the first real producer for the range model ---------------

def append_evidence(obs: List[LinkObservation], fails: List[LinkFailure],
                    base_dir: str = _WALK_DIR) -> None:
    """JSONL, append-only: a walk's truth survives the process that saw it.

    Every line is serialised BEFORE anything is written, with ``allow_nan``
    off (2026-09-23): a NaN distance or RSSI would otherwise land in the
    file as ``NaN`` — not JSON — and _load_jsonl would skip that line for
    ever, silently. Refusing raises ValueError out of here, which end_walk
    says on the glass ("Could not save the walk"); and because nothing has
    been opened yet, a refused walk leaves the files exactly as they were.
    """
    obs_lines = [json.dumps(o.to_dict(), allow_nan=False) + "\n" for o in obs]
    fail_lines = [json.dumps(f.to_dict(), allow_nan=False) + "\n"
                  for f in fails]
    base = os.path.expanduser(base_dir)
    os.makedirs(base, exist_ok=True)
    with open(os.path.join(base, _OBS_FILE), "a") as fh:
        fh.writelines(obs_lines)
    with open(os.path.join(base, _FAIL_FILE), "a") as fh:
        fh.writelines(fail_lines)


def _load_jsonl(path: str) -> List[dict]:
    """Every line that parses. A power cut mid-append leaves half a line;
    one such line used to raise out of here and the only caller dropped
    EVERY walk ever banked, silently (audit, 2026-09-21). Bad lines are
    skipped and counted, never fatal."""
    out: List[dict] = []
    try:
        with open(path) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    _SKIPPED.append(path)
    except OSError:
        return []
    return out


#: Paths of lines the loaders could not read — a count the screen may show.
_SKIPPED: List[str] = []


def _records(cls, dicts):
    """from_dict per record, dropping the ones that lack a field — the
    file format has grown before and will again."""
    out = []
    for d in dicts:
        try:
            out.append(cls.from_dict(d))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def load_walk_observations(base_dir: str = _WALK_DIR) -> List[LinkObservation]:
    base = os.path.expanduser(base_dir)
    return _records(LinkObservation, _load_jsonl(os.path.join(base, _OBS_FILE)))


def load_walk_failures(base_dir: str = _WALK_DIR) -> List[LinkFailure]:
    base = os.path.expanduser(base_dir)
    return _records(LinkFailure, _load_jsonl(os.path.join(base, _FAIL_FILE)))
