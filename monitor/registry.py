"""Node registry — the VITALS mode backend.

Holds known nodes keyed by their ``rtnode.health`` destination hash (the stable
identity the firmware persists in LittleFS). Static metadata — name, location,
type — is set at build time (the "birth certificate"); volatile health arrives
as decoded beacons. Status is the beacon's traffic-light, overridden to red
once a node hasn't been heard for longer than the staleness window.

Timestamps are passed in (epoch seconds) rather than read from the clock, so
the backend is deterministic and unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from monitor.health_beacon import HealthBeacon, beacon_status, decode
from monitor.health_poll import PollResult
from monitor.http_status import NodeStatus, PI_FORK
from monitor.geo import navigation_links
from ui import theme

#: Not heard for longer than this -> red (matches the Monitor spec).
STALE_ALERT_HOURS = theme.NOT_HEARD_ALERT_HOURS  # 18
#: A backward clock step this small is benign jitter, not "the reading predates
#: the clock". Without a deadband ANY raw < 0 (even a 3-second step behind a
#: node heard 2 seconds ago) would flip a LIVE node to grey "SEEN ?" — a
#: dishonesty the other way (crying unknown on a healthy node). Only a step
#: LARGER than this counts as a genuine clock-step worth flagging; inside the
#: band we clamp to 0.0 and treat the node as fresh.
SEEN_CLOCK_SKEW_TOLERANCE_S = 120        # two minutes
_SEEN_SKEW_TOLERANCE_H = SEEN_CLOCK_SKEW_TOLERANCE_S / 3600.0
#: Not heard for longer than this -> "quiet": drops below the VITALS divider but
#: is NOT necessarily red yet. A softer, recency-only signal (a ping lifts it back).
QUIET_AFTER_HOURS = theme.QUIET_AFTER_HOURS  # 12

_BEACON_RE = re.compile(
    r"\[HealthBeacon\][^\n]*dst=([0-9a-fA-F]+)[^\n]*data=([0-9a-fA-F]+)")

_STATUS_RANK = {"alert": 0, "warn": 1, "ok": 2, "unknown": 3}


def _capabilities(members) -> dict:
    """{lora, wifi, bluetooth, internet}: True = seen working, False = the
    node itself reports it down, None = unknowable from here (renders grey)."""
    lora = wifi = internet = bluetooth = None
    for r in members:
        iface = r.mesh_interface or ""
        if "RNode" in iface:
            lora = True                       # heard over the radio: proof
        if "TCPInterface" in iface and internet is None:
            internet = True                   # reached via an internet link
        http, beacon = r.latest_http, r.latest_beacon
        if http is not None and http.reachable:
            # A KEY THE NODE DID NOT SEND IS NOT A KEY THE NODE SET TO FALSE.
            # An RTNode-2400 sends all three every time, so for it these guards
            # are always taken and nothing about it changes. A Pi propagation
            # node omits any link it could not read (monitor.pi_status_server),
            # and without these guards its silence arrived here as False —
            # "reported down", drawn in amber, sending an operator to fix a
            # thing that was never measured. Unknown belongs in the same grey
            # None as everything else nobody has asked about.
            if http.lora_known and http.lora_online:
                lora = True                     # the node self-reports its LoRa:
                                                # honest LIVE detection, not a guess
            if http.wifi_known:
                wifi = bool(http.wifi_connected)
            if http.backbone_known:
                internet = bool(http.tcp_backbone_connected) or (internet is True)
        elif beacon is not None:
            if beacon.lora_up:                 # the node self-reports LoRa is up
                lora = True                     # (beacon flags byte, bit1) — honest live
            if wifi is None:
                wifi = bool(beacon.wifi_up)
            if internet is None:
                internet = bool(beacon.tcp_backbone_up)
            # Bluetooth: the KNOWN/UP bit pair (power-flags byte) — None on
            # beacons from firmware that predates the bits, so old nodes
            # stay honestly grey instead of falsely amber (SolarLove rule).
            if bluetooth is None and getattr(beacon, "bt_up", None) is not None:
                bluetooth = beacon.bt_up
    # NOTHING IS ADDED HERE. Everything above came from the node itself — heard
    # over an interface, or self-reported in its own health beacon or /status.
    #
    # THIS USED TO PROMOTE DECLARED CAPABILITIES TO TRUE. A kin node's board
    # type was looked up in DEFAULT_LINKS ("a Pi 3 A+ has wifi and bluetooth")
    # and anything the node had not spoken about was filled in as working. The
    # docstring above already promised the honest three states; the guess was
    # bolted on beneath it.
    #
    # It caught up with us on SolarLove, 2026-08-10: VITALS showed BT for a node
    # whose Bluetooth adapter was rfkill-blocked and had never been asked. The
    # operator spotted it — "SolarLove doesn't have Bluetooth access" — and drew
    # the rule this now follows: nothing is stated unless it is true, and what
    # is true is what the NODE said, never what its board could in principle do.
    #
    # A board's datasheet is not a node's state. An interface the node has not
    # mentioned stays None and renders as unknown, which is the truth.
    return {"lora": lora, "wifi": wifi, "bluetooth": bluetooth, "internet": internet}


def name_key(name: str) -> str:
    """A display name reduced to what two records must share to be one node.

    Case was already folded here; punctuation was not, and that was enough.
    SkyFinger — one Pi, one machine — sat in VITALS as two rows: ``SkyFinger``
    from its birth certificate and ``skyfinger!`` from what it announced. The
    operator has one node and the screen showed two, one of them looking half
    dead because only the other was being heard from.

    Kin names are the operator's own unique labels, so collapsing punctuation
    and spacing between them is safe; what is NOT safe is inventing a merge
    where no name exists at all, which is why an empty key never groups anything
    (see ``_device_groups``).
    """
    return "".join(ch for ch in (name or "") if ch.isalnum()).lower()


#: Placeholder subtitles for rows the medic has no real location for. They
#: sit in the "location" slot on VITALS but are NOT places: any prose that
#: says "...at {location}" (ui.app's node-watch escalation) must filter them
#: by these exact strings, so they live here once instead of drifting apart
#: as literals.
HEARD_ON_MESH = "heard on the mesh"
PROPAGATION_SUBTITLE = "LXMF propagation announces"


def _valid_display_name(text) -> bool:
    """Could *text* have been MEANT as a name? Printable throughout, sane
    length, and an alphanumeric residue of at least two characters — because
    ``name_key`` reduces a name to that residue before deciding whether two
    records are one node, a one-character residue is also a merge hazard, not
    just an ugly label. String-level so a name that survived to disk can be
    re-judged on load (the original bytes are gone by then)."""
    t = (text or "").strip()
    if not (2 <= len(t) <= 32):
        return False
    if any(not c.isprintable() for c in t):
        return False
    return len(name_key(t)) >= 2


def _printable_name(app_data) -> str:
    """A human display name from announce app_data, ONLY when the bytes are a
    clean UTF-8 name (LXMF prefixes a length byte before a UTF-8 name).

    THIS USED TO DECODE WITH errors="ignore" AND KEEP WHATEVER PRINTABLE
    CHARACTERS SURVIVED. Binary app_data — msgpack LXMF announces — shed its
    unprintable bytes and the residue was shown as a node's NAME: the
    operator's VITALS carried grey rows called "j(" and "j-(" on the night of
    2026-08-14. A name is a claim about what somebody called a thing; residue
    of a lossy decode is not that, and two different nodes' residues can even
    collide (name_key("j(") == name_key("j-(")), which is a merge-by-name of
    strangers waiting to happen. So: strict UTF-8 — a decode error is a
    refusal, not a cleanup job — and nothing unprintable survives. The only
    concession to the wire format is stripping ONE leading length/control byte
    (<= 0x20), which is the LXMF prefix convention, never arbitrary garbage
    ahead of a readable tail. Refused names fall back to "Neighbour <hash8>"
    downstream, which is the honest label.
    """
    if not app_data:
        return ""
    raw = bytes(app_data)
    candidates = [raw]
    if len(raw) >= 2 and raw[0] <= 0x20:      # LXMF length byte / control char
        candidates.append(raw[1:])
    for candidate in candidates:
        try:
            text = candidate.decode("utf-8").strip()
        except (UnicodeDecodeError, ValueError):
            continue
        if any(not c.isprintable() for c in text):
            continue
        if _valid_display_name(text):
            return text
    return ""


def _propagation_shape_fallback(raw: bytes):
    """Byte-level stand-in for msgpack when RNS is not importable (a dev Mac
    without the radio stack): reads the leading type-tags of MINIMALLY-ENCODED
    msgpack — what CPython's LXMF emits and what both live captures carry —
    and returns the decoded prefix as a list, or ``None`` on any mismatch.

    Deliberately narrower than a real decoder: a uint64 timestamp (0xCF) or
    an array16 header (0xDC) encodes the same values wider than necessary and
    is only understood on the RNS path in ``_is_propagation_announce``. That
    divergence is a decision, not an accident — no known emitter pads its
    encoding, and a fallback that grows toward a full msgpack decoder stops
    being a fallback.
    """
    def _uint(i):
        """One unsigned msgpack int at *i* -> (next_index, value) or None."""
        if i >= len(raw):
            return None
        b = raw[i]
        if b <= 0x7F:                                  # positive fixint
            return i + 1, b
        if b == 0xCC and i + 1 < len(raw):             # uint8
            return i + 2, raw[i + 1]
        if b == 0xCD and i + 2 < len(raw):             # uint16
            return i + 3, int.from_bytes(raw[i + 1:i + 3], "big")
        if b == 0xCE and i + 4 < len(raw):             # uint32
            return i + 5, int.from_bytes(raw[i + 1:i + 5], "big")
        return None

    try:
        # fixarray(>=6), bool, uint32 timestamp (every plausible-era unix
        # time, 2015-2100, is a msgpack uint32 when minimally encoded), bool.
        if len(raw) < 8 or (raw[0] & 0xF0) != 0x90 or (raw[0] & 0x0F) < 6:
            return None
        if raw[1] not in (0xC2, 0xC3) or raw[2] != 0xCE:
            return None
        ts = int.from_bytes(raw[3:7], "big")
        if raw[7] not in (0xC2, 0xC3):
            return None
        i = 8
        limits = []
        for _ in range(2):                             # obj[3], obj[4]: uints
            step = _uint(i)
            if step is None:
                return None
            i, v = step
            limits.append(v)
        if i >= len(raw) or raw[i] != 0x93:            # obj[5]: exactly-3 array
            return None
        i += 1
        triple = []
        for _ in range(3):                             # ...of unsigned ints
            step = _uint(i)
            if step is None:
                return None
            i, v = step
            triple.append(v)
        return [raw[1] == 0xC3, ts, raw[7] == 0xC3, limits[0], limits[1],
                triple]
    except Exception:
        return None


def _is_propagation_announce(app_data) -> bool:
    """Does this announce app_data carry the LXMF PROPAGATION-NODE payload?

    On 2026-08-22 two "anonymous neighbour" rows in VITALS turned out to be
    the medic's own two Pi relays — SKYFINGER and ELSEWHERE — wearing their
    THIRD identity. A Pi relay announces as three destinations: rnsd
    transport, the health reporter, and lxmd's ``lxmf.propagation`` aspect.
    The first two the registry can name; the third keeps its own identity
    file and announces a msgpack blob instead of a name, so it sat on the
    screen as a nameless stranger. The blob has a recognisable shape — a
    real capture decodes to:

        [False, 1787316090, True, 256, 10240, [16, 3, 18], {}]

    i.e. a list of at least six elements whose [0] is a bool, [1] a
    plausible unix timestamp, [2] a bool (propagation enabled), and [5] a
    triple of ints ([16, 3, 18] in the capture — treated structurally, no
    claim about what the numbers mean). Checking through [5] matters:
    ``[True, ts, True, {...}]`` — somebody's telemetry that merely opens the
    same way — must NOT be called a propagation relay. That shape is what
    the FORMAT proves, and it is ALL it proves — it never says whose machine
    it is, so the label downstream stays "Propagation relay", not a name.

    Decode with Reticulum's own vendored msgpack when it is importable
    (``import RNS`` is heavy, so it happens in here, and its absence — a
    dev Mac with no radio stack — is just the fallback path, never a
    crash): see ``_propagation_shape_fallback`` for what the byte-level
    stand-in does and deliberately does not accept.
    """
    try:
        raw = bytes(app_data or b"")
        if not raw:
            return False
        try:
            from RNS.vendor import umsgpack        # heavy: only on demand
            obj = umsgpack.unpackb(raw)
        except Exception:
            obj = _propagation_shape_fallback(raw)
        if not isinstance(obj, (list, tuple)) or len(obj) < 6:
            return False
        if not isinstance(obj[0], bool) or not isinstance(obj[2], bool):
            return False
        ts = obj[1]
        if isinstance(ts, bool) or not isinstance(ts, int):
            return False
        ver = obj[5]
        if not isinstance(ver, (list, tuple)) or len(ver) != 3:
            return False
        if not all(isinstance(v, int) and not isinstance(v, bool)
                   for v in ver):
            return False
        return 1420070400 <= ts <= 4102444800      # 2015-01-01 .. 2100-01-01
    except Exception:
        return False


def version_tuple(v: str):
    """Parse a dotted version ("0.6.2") into a comparable int tuple."""
    out = []
    for part in str(v).split("."):
        m = re.match(r"\d+", part)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


@dataclass
class CommissionEvent:
    """One entry in a node's provisioning history / field log."""
    at: float          # epoch seconds
    kind: str          # build | repair | fix | onboard | note | ...
    summary: str
    operator: str = "operator"


@dataclass
class NodeRecord:
    dst_hash: str
    name: str = ""
    location: str = ""
    node_type: str = "rtnode2400"          # "rtnode2400" | "pi"
    latest_beacon: Optional[HealthBeacon] = None
    latest_http: Optional[NodeStatus] = None   # last HTTP /status poll (LAN)
    mesh_hops: Optional[int] = None            # reachable via the LoRa mesh
    mesh_interface: str = ""
    last_seen: Optional[float] = None       # epoch seconds
    #: When the node was last heard ON THE MESH, from the path table row's own
    #: timestamp. Kept apart from last_seen because the two decay differently: a
    #: path lives seven days after the announce that taught it, so "still in the
    #: table" is not "still alive". Held separately so a scan can CORRECT a
    #: record that an earlier version stamped with now — see ingest_mesh.
    mesh_heard: Optional[float] = None
    #: When the node last spoke to us directly — a health beacon, an HTTP poll,
    #: an announce. Stronger evidence than a route, and never overwritten by one.
    last_direct: Optional[float] = None
    #: When a DIRECT interrogation (the operator's ping / 0x01 poll) last went
    #: unanswered. Newest-evidence rule: while this is fresher than last_seen,
    #: the node must not wear a clean green face — the tool itself just failed
    #: to raise it (seed, powered off but green, 2026-08-20). Cleared by any
    #: newer beacon, HTTP poll, or answered probe.
    poll_failed_at: Optional[float] = None
    #: When a byte-identical copy of the last beacon was heard again. A repeated
    #: payload is a RETRANSMISSION (rnsd re-emits cached announces on path
    #: requests), not the node speaking: on 2026-08-21 a wiped, battery-less
    #: board's row was "seen" 90 s after unplugging because of exactly this.
    #: Recorded for diagnosis; never evidence of life.
    last_echo_at: Optional[float] = None
    #: When a GENUINE (non-echo) announce or beacon from this node was last
    #: ingested — written ONLY by ingest()/ingest_announce's non-replay
    #: branches, never by path-table folds (ingest_mesh/ingest_relay) or HTTP
    #: polls. This is the ping reply-watch's oracle (health_poll.heard_since):
    #: last_seen also moves on mesh scans, so watching last_seen let a
    #: mid-window rnpath tick fake an answer from a silent node.
    last_heard_announce_at: Optional[float] = None
    #: sha256 hexdigest of the last BARE announce payload heard from this node
    #: (announces that don't decode as beacons). The bare-announce twin of the
    #: byte-identical beacon check: rnsd replays these from its cache too, and
    #: without a fingerprint to compare, ingest_announce laundered every replay
    #: into a genuine sighting. None = never heard one / no payload to print.
    last_announce_fp: Optional[str] = None
    lat: Optional[float] = None             # exact coords (from birth cert)
    lon: Optional[float] = None
    #: Whether this node PUBLISHES a position to the public mesh map — the
    #: operator's answer at birth, changeable later from node detail. "hidden"
    #: until somebody says otherwise, including for every record that predates
    #: the field: a node whose setting cannot be read has not consented to
    #: anything. What is published is always the fuzzed pin, never lat/lon
    #: above — see monitor.location_share.
    share_location: str = "hidden"
    #: When the medic last successfully WROTE this decision into the node's own
    #: config and read it back — not when the operator made it. The two are
    #: different, and the difference is a node on a roof still announcing after
    #: the operator has turned sharing "off" on a screen. None = never applied.
    share_applied_at: Optional[float] = None
    identity_hash: Optional[str] = None     # groups aspect-destinations per DEVICE
    #: Which physical MACHINE this destination belongs to, when the medic knows
    #: it for a fact rather than by inference. Stamped from the kin roster, which
    #: learns it at birth — the one moment the medic has the whole node in front
    #: of it and can see that its rnsd address and its health-beacon address are
    #: the same Pi.
    #:
    #: ``identity_hash`` cannot do this job for a Pi propagation node. The health
    #: reporter deliberately keeps its OWN identity file, separate from rnsd's,
    #: so the two destinations announce two genuinely different identities and no
    #: amount of listening will ever link them. That is why SkyFinger was two rows
    #: in VITALS for one machine.
    device_id: Optional[str] = None
    announced_name: str = ""                # name a neighbour announces (e.g. LXMF)
    #: This destination announces the LXMF propagation-node payload — see
    #: ``_is_propagation_announce``. Proves WHAT the destination is (an lxmd
    #: propagation relay), never WHOSE machine it is. Defaults False so a
    #: registry file written before the field existed loads unchanged.
    is_propagation: bool = False
    links: Optional[dict] = None            # KIN-declared interfaces the node HAS
                                            # ({lora,wifi,bluetooth,internet}: True)
    builder_hash: Optional[str] = None      # identity of the medic UNIT that
                                            # birthed it; its trust => kin/neighbour
    notes: List[str] = field(default_factory=list)
    events: List[CommissionEvent] = field(default_factory=list)

    @property
    def firmware_version(self) -> Optional[str]:
        if self.latest_http and self.latest_http.firmware_version:
            return self.latest_http.firmware_version
        return self.latest_beacon.firmware_version if self.latest_beacon else None

    def has_location(self) -> bool:
        return self.lat is not None and self.lon is not None

    def public_pin(self) -> Optional[tuple]:
        """The fuzzed point this node advertises, or ``None`` when it shares
        nothing / has no location. Never the exact coordinates."""
        from monitor import location_share
        if not location_share.is_shared(self.share_location):
            return None
        return location_share.public_pin(self.lat, self.lon,
                                         self.name or self.dst_hash)

    def navigation(self) -> Optional[dict]:
        """Turn-by-turn deep links to the node (from its exact birth-cert
        coordinates), or ``None`` if no location is on file."""
        if not self.has_location():
            return None
        return navigation_links(self.lat, self.lon)

    def needs_firmware_update(self, latest: str) -> bool:
        fw = self.firmware_version
        if fw is None:
            return False
        return version_tuple(fw) < version_tuple(latest)

    def _seen_age_raw(self, now: float) -> Optional[float]:
        """Hours since the freshest evidence (``last_seen``), or ``None`` when
        the node has NEVER been heard (set_kin_roster seeds fleet rows before
        first contact). May be NEGATIVE: a clock that stepped backwards behind
        the stored stamp makes ``now - last_seen < 0``, and that is not
        freshness — it means this reading PREDATES the current clock. The ONE
        place the raw age is computed, so the age accessor (last_seen_hours)
        and the status derivation (_status_base) can never disagree about
        whether a node is fresh — the sibling rule the echo/direct ages already
        hold ("both surfaces must agree by construction"). Reached here through
        the clock-step door: last_seen_hours did NOT clamp like its siblings,
        so a backward step floored to "SEEN 0.0h" GREEN — the 2026-08-21
        dead-board-green class, again (2026-08-22)."""
        if self.last_seen is None:
            return None
        return (now - self.last_seen) / 3600.0

    def last_seen_hours(self, now: float) -> Optional[float]:
        """Age of the freshest evidence, clamped to ``>= 0`` like
        last_echo_hours / last_direct_hours — a backward clock step must not
        turn "heard" into a negative age (format_age floors it to 0.0 and
        last_seen_status paints that GREEN). ``None`` stays ``None`` (never
        heard). The impossibility is not lost: to_dashboard carries a
        ``seen_impossible`` flag off the raw age so the screen renders
        "SEEN ?" grey rather than a clamped "0.0h" green."""
        raw = self._seen_age_raw(now)
        if raw is None:
            return None
        return max(0.0, raw)

    def last_echo_hours(self, now: float) -> Optional[float]:
        """Age of the last transport REPLAY, or ``None`` when none is on
        record. Same convention as ``last_seen_hours`` — but the two are never
        interchangeable: an echo is the mesh repeating the node's last words,
        not the node speaking (the dead board that stayed green, 2026-08-21)."""
        if self.last_echo_at is None:
            return None
        # Clamped: a clock stepping backwards must not turn "echo on record"
        # into a negative age that a display sentinel reads as "no echo".
        return max(0.0, (now - self.last_echo_at) / 3600.0)

    def last_direct_hours(self, now: float) -> Optional[float]:
        """Age of the node's last DIRECT word (beacon / HTTP / announce), or
        ``None``. This is what the echo tag is gated on — not last_seen, which
        a mesh scan may bump from a path row's learned-time (weaker evidence,
        see ingest_mesh). Clamped like last_echo_hours, and for the same
        reason: both surfaces must agree by construction."""
        if self.last_direct is None:
            return None
        return max(0.0, (now - self.last_direct) / 3600.0)

    def signal_dbm(self) -> Optional[int]:
        """Best available WiFi signal — HTTP /status first, then the beacon.
        None when WiFi is DOWN: the wire carries 0 for 'no reading', and
        surfacing that as a real 0 dBm (a colossal signal!) misled VITALS and
        poisoned the history graph (2026-08-01 bug hunt)."""
        h = self.latest_http
        if h is not None and h.wifi_rssi_dbm:      # 0 == "no reading"
            return h.wifi_rssi_dbm
        b = self.latest_beacon
        if b is not None and b.wifi_rssi_dbm:
            return b.wifi_rssi_dbm
        return None

    @property
    def provenance(self) -> str:
        """Kin/neighbour classification.

        When the birthing UNIT is KNOWN (``builder_hash`` set from this node's kin
        roster entry), trust decides: a trusted builder (incl. this medic's own
        unit = self) -> kin; a REVOKED builder -> neighbour. This is what makes
        Settings' revoke actually demote a unit's birthed nodes.

        When the builder is UNKNOWN (a heard mesh node, no roster), fall back to
        the interim heuristic (#54): a record that has spoken our protocols
        (beacon / HTTP status) or was named/located by an operator is KIN; a bare
        mesh-heard destination hash is a NEIGHBOUR."""
        if self.builder_hash is not None:
            from monitor import trust           # lazy: avoid an import cycle
            return trust.node_provenance(self.builder_hash, path=trust.CONFIG)
        ours = (self.latest_beacon is not None or self.latest_http is not None
                or bool(self.name) or self.lat is not None)
        return "kin" if ours else "neighbour"

    def to_dashboard(self, now: float) -> dict:
        """The node dict the VITALS screen (ui.screens.vitals_screen) renders.
        Pure + testable; the Kivy view just reads these keys. Honest: no
        invented numbers — unknown signal/battery stay None and the screen
        hides them; a bare mesh destination renders as a grey Neighbour, not a
        healthy green RTNode."""
        raw_seen = self._seen_age_raw(now)
        lsh = self.last_seen_hours(now)
        # Honesty flags for the SEEN line, mirroring the has_echo/has_direct
        # pattern: a NumericProperty on the StatBar can't carry "never heard"
        # or "predates the clock", so the truth rides as booleans instead of
        # being collapsed into a misleading 0.0.
        has_seen = raw_seen is not None            # False = never heard
        # Impossible only past the deadband: a sub-two-minute backward jitter is
        # not "predates the clock", so it stays fresh (see _status_base).
        seen_impossible = (raw_seen is not None
                           and raw_seen < -_SEEN_SKEW_TOLERANCE_H)
        sig = self.signal_dbm()
        neighbour = self.provenance == "neighbour"
        status = self.status(now)
        if neighbour and status == "ok":
            status = "unknown"           # heard != healthy; we know nothing yet
        display = self.name or (
            (self.announced_name or f"Neighbour {self.dst_hash[:8]}")
            if neighbour else "(unnamed)")
        where = self.location or (HEARD_ON_MESH if neighbour else "")
        # A nameless neighbour whose announces carry the LXMF propagation
        # payload gets called what the format PROVES it is — a propagation
        # relay — and nothing more. Never which machine: SKYFINGER and
        # ELSEWHERE each wear one of these as a third identity (2026-08-22),
        # but the announce itself cannot say so, so neither do we. Any
        # operator-given or announced name still wins.
        if neighbour and self.is_propagation and not (self.name
                                                      or self.announced_name):
            display = f"Propagation relay {self.dst_hash[:8]}"
            where = self.location or PROPAGATION_SUBTITLE
        return {
            "name": display,
            "location": where,
            "status": status,
            "type": self.node_type,
            "provenance": self.provenance,
            "identity": self.dst_hash,    # kin key — lets a VITALS tap adopt it

            "signal_dbm": sig,                      # None = never measured
            "last_seen_hours": lsh if lsh is not None else 0.0,
            # ...but 0.0 is a lie for a never-heard or clock-stepped row, so
            # these flags carry the truth the number can't (rendered as
            # "SEEN never" / "SEEN ?" grey, never "0.0h" green).
            "has_seen": has_seen,
            "seen_impossible": seen_impossible,
            # The replay age rides along so the screen can show an echo for
            # what it is — muted, informational, and NEVER an input to the
            # status colour above (which was computed before this line and
            # does not read last_echo_at). That separation is the fix for
            # 2026-08-21: a powered-off, battery-less board stayed green for
            # hours because replays kept "seeing" it. The direct age rides
            # with it because the tag is gated on the node's last DIRECT word
            # (formatting.seen_and_echo) — last_seen can carry a mesh scan's
            # path-learned time, which is not the node speaking either.
            "last_echo_hours": self.last_echo_hours(now),     # None = no echo
            "last_direct_hours": self.last_direct_hours(now),  # None = never
            "battery_pct": self._battery_pct(),
            "powered_by": self._powered_by(),
        }

    def _battery_pct(self) -> Optional[int]:
        """Battery charge from the v2 beacon — it was decoded and then thrown
        away by a hardcoded None (2026-08-01 bug hunt), so a solar node's
        charge never reached VITALS."""
        b = self.latest_beacon
        pct = getattr(b, "battery_pct", None) if b is not None else None
        return pct if isinstance(pct, int) else None

    def _powered_by(self) -> str:
        """How the node is running, from the v2 beacon's power flags."""
        b = self.latest_beacon
        if b is not None:
            if getattr(b, "on_solar", False):
                return "solar"
            if getattr(b, "on_mains", False):
                return "mains"
            if getattr(b, "on_battery", False):
                return "battery"
        return "battery"

    @property
    def probe_unanswered(self) -> bool:
        """The freshest DIRECT evidence about this node is a failed
        interrogation — nothing heard from it since a probe went unanswered."""
        return (self.poll_failed_at is not None
                and (self.last_seen is None
                     or self.poll_failed_at > self.last_seen))

    def status(self, now: float) -> str:
        base = self._status_base(now)
        # An unanswered probe outranks a stale-but-green beacon: the operator
        # ASKED and the node did not answer. It demotes a clean face to warn;
        # it never upgrades warn/alert (worse evidence stands).
        if self.probe_unanswered and base in ("ok", "unknown"):
            return "warn"
        return base

    def _status_base(self, now: float) -> str:
        raw = self._seen_age_raw(now)
        if raw is None:
            return "unknown"
        if raw < -_SEEN_SKEW_TOLERANCE_H:
            # The freshest stamp predates the current clock by MORE than benign
            # jitter (a genuine backward step): not freshness, a clock warning.
            # NEVER green off such an age — last_seen_hours clamps the SAME raw
            # value and to_dashboard flags it on the SAME threshold, so the SEEN
            # icon and this hexagon agree by construction (the sibling rule,
            # reached through the clock-step door 2026-08-22). A sub-tolerance
            # step falls through and is treated as fresh.
            return "unknown"
        if raw > STALE_ALERT_HOURS:
            return "alert"                  # not heard -> red, regardless
        # Prefer the richer HTTP /status (has an explicit faults array) when a
        # node is LAN-reachable; then the mesh beacon; then bare mesh
        # reachability (in the path table = reachable, health unknown -> ok).
        if self.latest_http is not None and self.latest_http.reachable:
            return self.latest_http.status
        if self.latest_beacon is not None:
            return beacon_status(self.latest_beacon)
        if self.mesh_hops is not None:
            return "ok"                     # reachable over the mesh
        return "unknown"


def _share_policy(value) -> str:
    """A stored sharing answer, or "hidden" if there isn't one. Module-level so
    the dataclass rebuild in ``from_dict`` doesn't have to import inside a
    comprehension."""
    from monitor import location_share
    return location_share.normalise(value)


def _locked(fn):
    """Hold the registry lock for the whole call. Applied to every method that
    MUTATES nodes/history or WALKS them — the announce thread inserting a key
    mid-iteration is the race (2026-08-01 bug hunt). The lock is an RLock, so
    a guarded reader may call other guarded helpers."""
    import functools

    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self._lock:
            return fn(self, *a, **kw)
    return wrapper


class NodeRegistry:
    def __init__(self):
        # ONE lock guards nodes + history. Three threads touch this registry:
        # the RNS announce handler (inserts new keys), the monitor poll thread
        # (saves/dashboards) and the Kivy main thread (VITALS/SCAN/TRIAGE
        # reads) — an announce arriving mid-iteration raised "dictionary
        # changed size during iteration" (reproduced by the 2026-08-01 bug
        # hunt). RLock so a locked reader can call a locked helper.
        import threading
        self._lock = threading.RLock()
        self.nodes: Dict[str, NodeRecord] = {}
        from monitor.history import NodeHistory
        self.history = NodeHistory()    # per-node time series (VITALS "History")
        #: The medic's own fleet, keyed by RNS hash (monitor.kin_roster). Any
        #: record whose hash is in here is authoritatively named/typed/located as
        #: KIN — even a plain propagation Pi the medic can't hear directly.
        self.kin_roster: Dict[str, dict] = {}
        #: THIS medic's OWN identity hashes (full lowercase hex) — its rnsd
        #: transport identity and its lxmd/LXMF-propagation identity. An announce
        #: whose identity is in here is the medic HEARING ITSELF, not a neighbour:
        #: on 2026-08-22 the medic's own lxmd destination (identity 5a180018)
        #: surfaced in VITALS as an anonymous "Propagation relay". Populated at
        #: startup from provisioning.tool_identity.own_identity_hashes(); empty by
        #: default so an un-wired registry (and every test that doesn't set it)
        #: filters nobody. Matched by IDENTITY, so a node the medic BUILT — which
        #: carries a DIFFERENT identity — is never mistaken for the medic itself.
        self.own_identities: set = set()

    @_locked
    def set_own_identities(self, hashes) -> None:
        """Record THIS medic's OWN identity hashes (see ``own_identities``) so
        its own announces are never mistaken for neighbours. Idempotent;
        normalises to lowercase hex and drops empties. The app wires this at
        startup from provisioning.tool_identity.own_identity_hashes()."""
        self.own_identities = {str(h).lower() for h in (hashes or ()) if h}

    def _is_own_identity(self, rec) -> bool:
        """True if *rec*'s identity is one of THIS medic's own — the guard that
        keeps the medic's own destinations out of both ingest and the display.
        Matched by identity hash, so kin (a DIFFERENT identity) never match."""
        if not self.own_identities:
            return False
        ih = getattr(rec, "identity_hash", None)
        return bool(ih and ih.lower() in self.own_identities)

    #: Every NodeRecord field that stores a WALL-CLOCK epoch. Rebased as a set
    #: after the system clock is stepped (GPS discipline) so they stay in the new
    #: clock's frame — otherwise a forward step makes every node look silent for
    #: the step size (false escalations, false "SEEN ?"/false-fresh) — exactly
    #: the dishonesty the liveness work removed.
    _WALL_STAMP_FIELDS = (
        "last_seen", "mesh_heard", "last_direct", "poll_failed_at",
        "last_echo_at", "last_heard_announce_at", "share_applied_at",
    )

    @_locked
    def rebase_wall_clock(self, delta: float) -> None:
        """Shift every stored wall-clock stamp — every record's time fields, its
        commission-event timestamps, and every history point — by *delta* seconds
        after the system clock is stepped. delta = new_epoch - old_epoch.

        Without this, a GPS step re-creates the very false-escalation and
        false-freshness bugs tonight's work removed: a node heard "5 min ago"
        would read "3 hours ago" after a 3-hour forward step, and history.py's
        retention would prune genuine points. Best-effort and idempotent-safe: a
        zero delta is a no-op."""
        if not isinstance(delta, (int, float)) or isinstance(delta, bool) or not delta:
            return
        # Guard every addition: a stamp is normally a float, but a corrupt record
        # could hold anything, and this runs right after the clock was stepped —
        # it must NEVER raise (the caller treats a raise as a half-applied step).
        def _shift(v):
            return (v + delta) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        for rec in self.nodes.values():
            for attr in self._WALL_STAMP_FIELDS:
                setattr(rec, attr, _shift(getattr(rec, attr, None)))
            for ev in rec.events:              # field-log timestamps track wall time too
                ev.at = _shift(getattr(ev, "at", None))
        self.history.rebase(delta)

    @_locked
    def set_kin_roster(self, roster: dict) -> None:
        """Load the medic's fleet roster. Seeds a NAMED, LOCATED record for every
        entry (so each fleet node shows in VITALS as kin and on the map at its
        deployed spot, even before it's heard) and re-applies it to any record
        already present."""
        self.kin_roster = dict(roster or {})
        for h in self.kin_roster:
            if h not in self.nodes:
                self.register(h)                 # register() applies kin itself
        # EVERY record, not only the ones the roster keys directly. A record
        # heard before the birth was written down (the medic hears the bench
        # announce while the certificate is still being assembled) is linked to
        # its roster entry only by its announced IDENTITY — reloading the
        # roster must fold those too, or they stay grey until the next announce
        # (2026-08-14, the night three births sat in VITALS as strangers).
        for rec in self.nodes.values():
            self._apply_kin(rec)

    def _apply_kin(self, rec: NodeRecord) -> None:
        """If this record is one of the medic's own nodes, stamp its roster name,
        type, and deployed location — making it kin (named) and map-visible.

        Matched by destination hash, or — failing that — by the record's
        ANNOUNCED IDENTITY. A birth writes down the hashes the CERTIFICATE
        carries, but a node announces destinations the certificate never saw:
        a Pi's lxmf aspect, any destination minted after the paperwork. Every
        announce carries the identity RNS itself verified, and the roster's
        keys include identities (a Pi cert's ``reticulum_address``, an adopted
        node's key) — so folding by identity is identity evidence, the same
        strength as the dst match, and NOT a name merge. On 2026-08-14 the
        missing identity path left the operator's own newborn nodes' announce
        destinations sitting in VITALS as bare grey "Neighbour" rows.
        """
        entry = self.kin_roster.get(rec.dst_hash)
        if not entry and rec.identity_hash:
            # THE ROSTER BY IDENTITY TOO (build-lens, 2026-08-13): a machine
            # announces destinations the roster never listed, but they carry
            # the identity the roster's entries were announced under. Without
            # this, an aspect sibling stayed an anonymous second row.
            entry = self.kin_roster.get(rec.identity_hash)
        if not entry:
            return
        if entry.get("name"):
            rec.name = entry["name"]
        if entry.get("type"):
            rec.node_type = entry["type"]
        if entry.get("lat") is not None:
            rec.lat = entry["lat"]
        if entry.get("lon") is not None:
            rec.lon = entry["lon"]
        if entry.get("share_location") is not None:
            from monitor import location_share
            rec.share_location = location_share.normalise(
                entry["share_location"])
        if entry.get("links"):
            rec.links = entry["links"]
        if entry.get("builder"):
            rec.builder_hash = entry["builder"]
        if entry.get("device"):
            rec.device_id = entry["device"]

    @_locked
    def ingest_relay(self, via_hash: str, interface: str, now: float,
                     heard: float = 0.0) -> NodeRecord:
        """Surface the medic's DIRECT next-hop relay (a ``via`` in the path table)
        as a node. A via is the medic's 1-hop LoRa neighbour that the whole mesh
        routes through — e.g. EVERYWHERE — yet it's never a destination in rnpath,
        so without this it stays invisible. Marks it reachable (1 hop) and applies
        the kin roster (names it if it's ours).

        BEING NAMED AS A VIA IS NOT BEING SEEN — the same seven-day-table rule
        ingest_mesh learned from SolarLove (2026-08-11). This used to stamp
        last_seen AND last_direct with *now* on every rediscover tick, which
        kept a dead relay wearing a fresh face — and could land inside a ping's
        reply window and fake an answer. What IS evidence is *heard*: the
        destination row's own timestamp, when that announce arrived over the
        air as this via's transmission. Best (newest) heard wins; last_direct
        is never touched — a route relayed is not the relay speaking about
        itself. *now* is kept for signature uniformity with the other ingests
        and deliberately stamps nothing.
        """
        del now  # explicit: table presence carries no timestamp evidence
        rec = self.nodes.get(via_hash) or self.register(via_hash)
        rec.mesh_hops = 1
        if interface:
            rec.mesh_interface = interface
        if heard:
            # Several destination rows can name the same via with different
            # ages; pool them freshest-wins, and never move last_seen back.
            if rec.mesh_heard is None or heard > rec.mesh_heard:
                rec.mesh_heard = heard
            direct = rec.last_direct or 0.0
            rec.last_seen = max(direct, rec.mesh_heard) or None
        self._apply_kin(rec)
        return rec

    @_locked
    def register(self, dst_hash: str, name: str = "", location: str = "",
                 node_type: str = "rtnode2400", lat: Optional[float] = None,
                 lon: Optional[float] = None,
                 share_location: Optional[str] = None) -> NodeRecord:
        """Create or update a node's static metadata (from the birth cert)."""
        from monitor import location_share as _ls
        rec = self.nodes.get(dst_hash)
        if rec is None:
            rec = NodeRecord(dst_hash=dst_hash, name=name, location=location,
                             node_type=node_type, lat=lat, lon=lon,
                             share_location=_ls.normalise(share_location))
            self.nodes[dst_hash] = rec
        else:
            # Only an EXPLICIT answer changes it. Passing None (every existing
            # caller) must never quietly reset a node's sharing decision — nor
            # quietly turn one on.
            if share_location is not None:
                rec.share_location = _ls.normalise(share_location)
            if name:
                rec.name = name
            if location:
                rec.location = location
            rec.node_type = node_type
            if lat is not None:
                rec.lat = lat
            if lon is not None:
                rec.lon = lon
        self._apply_kin(rec)
        return rec

    @_locked
    def register_from_birth_certificate(self, cert: dict, name: str = "",
                                        now: float = 0.0,
                                        operator: str = "operator"
                                        ) -> Optional[NodeRecord]:
        """Register a freshly-built node from its birth certificate: exact
        coordinates, and a 'build' entry in the commissioning log."""
        dst = cert.get("identity_hash")
        if not dst:
            return None
        loc = cert.get("location") or {}
        # The birth answer travels WITH the certificate, so a node adopted from
        # its own paperwork keeps the decision its operator made, instead of
        # silently reverting to whatever this registry's default happens to be.
        rec = self.register(dst, name=name, node_type="rtnode2400",
                            lat=loc.get("lat"), lon=loc.get("lon"),
                            share_location=loc.get("share_location"))
        self.log_event(
            dst, "build",
            f"Provisioned {cert.get('board', '')} fw {cert.get('firmware', '')}",
            now, operator)
        return rec

    def get(self, dst_hash: str) -> Optional[NodeRecord]:
        return self.nodes.get(dst_hash)

    @_locked
    def set_share_location(self, dst_hash: str, policy: str,
                           now: float = 0.0,
                           operator: str = "operator") -> Optional[NodeRecord]:
        """Change a node's map-sharing decision AFTER birth — the operator who
        put a node on a roof and then thought better of it must not have to
        rebirth it to change their mind.

        Logged in the commissioning history, because turning a position on is
        the kind of thing whoever inherits this node deserves to be able to find
        out about, and turning it off is not the same as never having sent one.
        """
        from monitor import location_share
        rec = self.nodes.get(dst_hash)
        if rec is None:
            return None
        was = rec.share_location
        rec.share_location = location_share.normalise(policy)
        if rec.share_location != was:
            # A NEW DECISION IS UNAPPLIED UNTIL IT IS APPLIED. Leaving the old
            # timestamp would make a node that has just been switched off read
            # as "off and written to the node" while it carries on announcing.
            rec.share_applied_at = None
            rec.events.append(CommissionEvent(
                now, "location",
                ("Map sharing ON — publishes a fuzzed point, not its real "
                 "position" if location_share.is_shared(rec.share_location)
                 else "Map sharing OFF — no further position announces"),
                operator))
        return rec

    @_locked
    def mark_share_applied(self, dst_hash: str, now: float
                           ) -> Optional[NodeRecord]:
        """Record that the decision is now ON THE NODE — called only after a
        write was made AND read back (monitor.location_share.push_to_node)."""
        rec = self.nodes.get(dst_hash)
        if rec is None:
            return None
        rec.share_applied_at = now
        return rec

    @_locked
    def ingest(self, dst_hash: str, beacon: HealthBeacon,
               now: float) -> NodeRecord:
        """Record a decoded beacon; auto-registers a never-seen node."""
        rec = self.nodes.get(dst_hash)
        if rec is None:
            rec = self.register(dst_hash)
        if (rec.latest_beacon is not None
                and rec.latest_beacon.to_bytes() == beacon.to_bytes()):
            # REPLAY, NOT A SIGHTING. A live node's beacon always differs from
            # its last (uptime_s ticks); identical bytes mean the transport
            # replayed a cached announce. It must not refresh last_seen, must
            # not cure a failed poll, and must not fake a history point.
            rec.last_echo_at = now
            return rec
        rec.latest_beacon = beacon
        rec.last_seen = now
        rec.last_heard_announce_at = now   # the node itself, freshly heard
        if rec.poll_failed_at is not None and rec.last_seen is not None \
                and rec.last_seen >= rec.poll_failed_at:
            rec.poll_failed_at = None   # newer direct word from the node itself
        rec.last_direct = now
        from monitor.history import HistoryPoint
        self.history.append(dst_hash, HistoryPoint(
            t=now,
            rssi=(beacon.wifi_rssi_dbm or None),   # 0 == "no reading"
            uptime_s=beacon.uptime_s))
        return rec

    @_locked
    def ingest_line(self, text: str, now: float) -> Optional[NodeRecord]:
        """Parse a serial/announce ``[HealthBeacon]`` line and ingest it.
        Returns ``None`` for non-beacon or undecodable lines."""
        m = _BEACON_RE.search(text)
        if not m:
            return None
        try:
            beacon = decode(bytes.fromhex(m.group(2)))
        except ValueError:
            return None
        return self.ingest(m.group(1), beacon, now)

    @_locked
    def ingest_announce(self, dst_hash: bytes, app_data: bytes,
                        now: float,
                        identity_hash: Optional[str] = None) -> Optional[NodeRecord]:
        """Adapter for an RNS announce handler. A live handler does:

            def received_announce(self, destination_hash, identity, app_data):
                registry.ingest_announce(destination_hash, app_data, time.time())

        *dst_hash* is the raw destination-hash bytes RNS provides. Every
        announce marks the node HEARD (honest last-seen for neighbours) and
        records the announced identity (groups a device's aspect-destinations)
        and any announced display name. Beacon payloads additionally ingest
        as health data.
        """
        h = dst_hash.hex() if isinstance(dst_hash, (bytes, bytearray)) else str(dst_hash)
        # THE MEDIC IS NOT ITS OWN NEIGHBOUR. 2026-08-22 (live): the medic heard
        # its OWN lxmd propagation announce and listed its own destination
        # 5a0a000a (identity 5a180018) as an anonymous "Propagation relay". An
        # announce whose identity is one of THIS medic's own destinations is the
        # medic talking to itself — drop it before any record is created. Keyed
        # by IDENTITY: a node the medic BUILT carries a DIFFERENT identity, so
        # this never touches kin (the "EVERYWHERE was only ever a via" lesson).
        if identity_hash and self.own_identities \
                and identity_hash.lower() in self.own_identities:
            return None
        try:
            beacon = decode(app_data)
        except (ValueError, TypeError):
            beacon = None
        propagation = beacon is None and _is_propagation_announce(app_data)
        if beacon is not None:
            rec = self.ingest(h, beacon, now)
        else:
            rec = self.nodes.get(h) or self.register(h)
            import hashlib
            fp = (hashlib.sha256(bytes(app_data)).hexdigest()
                  if app_data else None)
            if (fp is not None and fp == rec.last_announce_fp
                    and rec.last_seen is not None):
                # REPLAY, NOT A SIGHTING — the bare-announce twin of the guard
                # in ``ingest``. rnsd re-emits cached announces byte-for-byte
                # on path requests, and this branch used to launder that copy
                # into a genuine sighting (last_seen AND last_direct) — which,
                # pooled across a multi-aspect device, buried the echo tag the
                # 2026-08-21 dead board earned. Identical payload bytes we
                # have already heard are the transport speaking, not the node.
                rec.last_echo_at = now       # recorded; nothing else moves
            else:
                if fp is not None:
                    rec.last_announce_fp = fp
                # A payloadless announce is exempt from replay detection: with
                # nothing to compare, "identical" cannot be established — and
                # never guess. It stays a sighting.
                rec.last_seen = now
                rec.last_direct = now
                rec.last_heard_announce_at = now   # genuine, not an echo
                # Record a bare heard-event point so intermittent / neighbour nodes
                # (which never send a beacon) still accumulate an activity time-series
                # — the raw material for the "when is this node usually up?" profile.
                from monitor.history import HistoryPoint
                self.history.append(h, HistoryPoint(t=now))
            if propagation:
                rec.is_propagation = True   # what the format proves; no owner
        if identity_hash:
            had_identity = rec.identity_hash == identity_hash
            rec.identity_hash = identity_hash
            if not had_identity:
                # identity_hash lands AFTER register() ran _apply_kin — so the
                # roster must get a second look now that the join key exists
                # (build-lens, 2026-08-13: the S3 two-row state). Two agents
                # found this independently, a day apart, from different angles.
                self._apply_kin(rec)
        if rec.announced_name and not _valid_display_name(rec.announced_name):
            rec.announced_name = ""     # residue the old decoder let through
        # A propagation payload is msgpack, not a name — _printable_name's
        # strict UTF-8 already refuses the real captures, but the refusal is
        # made explicit here so no future loosening of the name decoder can
        # ever resurrect the "j(" ghosts (2026-08-14) from THIS payload.
        if propagation:
            return rec
        name = _printable_name(app_data)
        if name and not rec.announced_name:
            rec.announced_name = name
        return rec

    @_locked
    def ingest_mesh(self, node, now: float) -> NodeRecord:
        """Fold a mesh path (a monitor.mesh.MeshNode) into the registry, keyed by
        its destination hash — the same key birthed/HTTP nodes use. Records
        reachability (hops, interface); auto-registers an unknown destination
        (its name stays the hash until a birth cert names it).

        BEING IN THE PATH TABLE IS NOT BEING SEEN. This used to set
        ``last_seen = now`` for every row, so a node read "SEEN 0.0h" for as
        long as its path lived — and Reticulum keeps a learned path for SEVEN
        DAYS after the announce that taught it. SolarLove sat green and "seen
        0.0h" in VITALS while unplugged (found by the operator, 2026-08-11; its
        row had been taught 19 hours earlier and had 148 hours left to run).
        That is the worst kind of wrong for this screen: it is the one place an
        operator looks to find out whether a node is still alive.

        The row's own ``timestamp`` is when the path was learned, which IS when
        we last heard from the node, so that is what a sighting means here. Never
        moves last_seen backwards — a health beacon or an HTTP poll is fresher
        evidence than the path that carried it.
        """
        rec = self.nodes.get(node.dst_hash) or self.register(node.dst_hash)
        rec.mesh_hops = node.hops
        rec.mesh_interface = node.interface
        heard = getattr(node, "heard", 0.0) or 0.0
        # No timestamp (an older rnpath) leaves the record alone rather than
        # inventing a sighting: not knowing is not the same as just now.
        if heard:
            rec.mesh_heard = heard
            # RECOMPUTED, not max()-ed against the existing value. Records
            # written by the earlier version already hold "now" from the last
            # scan, so a max() would defend that wrong number forever. Direct
            # evidence still wins — it is just held in its own field now, so a
            # route can never impersonate it.
            direct = rec.last_direct or 0.0
            rec.last_seen = max(direct, heard) or None
        return rec

    def record_http_status(self, key: str, status: NodeStatus,
                           now: float) -> NodeRecord:
        """Fold in an HTTP ``/status`` poll for a LAN-reachable node, keyed by
        *key* (the node's dst_hash for a known node, or a synthetic id for a
        discovered one). A reachable poll refreshes last_seen + the health;
        an unreachable one changes nothing (staleness takes it red on its own).
        Auto-registers a never-seen node and adopts its node_name."""
        rec = self.nodes.get(key) or self.register(key)
        if status.reachable:
            rec.latest_http = status
            rec.last_seen = now
            rec.last_direct = now
            # A hostile /status must NOT set an arbitrary-length/arbitrary-
            # content node name: the ANNOUNCE path already runs names through
            # _printable_name -> _valid_display_name (strict UTF-8, length
            # 2-32), so the HTTP path must judge them by the same gate before
            # adoption. Type-check FIRST — a non-string node_name is refused,
            # never allowed to raise on .strip()/.lower() (2026-08-22).
            name = status.node_name
            clean = (name.strip() if isinstance(name, str)
                     and _valid_display_name(name) else None)
            if clean and not rec.name:
                rec.name = clean
            if clean and not rec.device_id:
                # A REAL JOIN, NOT A COINCIDENCE OF SPELLING (build-lens,
                # 2026-08-13): when /status names a machine the roster knows,
                # the discovery row takes that machine's device id — the same
                # key birth wrote — instead of relying on the name collapse.
                low = clean.lower()
                for entry in self.kin_roster.values():
                    if ((entry.get("name") or "").strip().lower() == low
                            and entry.get("device")):
                        rec.device_id = entry["device"]
                        break
            # THE NODE JUST SAID WHAT IT IS, so stop calling it something else.
            # register() types an unknown key "rtnode2400", and that default
            # already labelled the first Pi propagation node ever built as an
            # RTNode-2400 once (see kin_roster.type_for_cert, 2026-08-10) — this
            # is the same default coming in through the discovery door. A record
            # the kin roster owns is left alone: the roster is the operator's own
            # record and outranks a self-report.
            fork = (status.raw or {}).get("fork")
            if fork == PI_FORK and rec.dst_hash not in self.kin_roster:
                rec.node_type = "pi_propagation"
        return rec

    def record_poll(self, dst_hash: str, result: PollResult,
                    now: float) -> Optional[NodeRecord]:
        """Fold an on-demand poll result in: a fresh reply updates the node
        (clearing red/orange to green if clean); SILENCE IS RECORDED TOO —
        an unanswered interrogation is the freshest evidence there is, and
        discarding it left a powered-off node wearing a green face for hours
        (seed, 2026-08-20)."""
        if result.reachable and result.beacon is not None:
            return self.ingest(dst_hash, result.beacon, now)
        return self.record_probe(dst_hash, ok=False, now=now)

    @_locked
    def record_probe(self, dst_hash: str,
                     ok: bool, now: float) -> Optional[NodeRecord]:
        """Record the OUTCOME of a direct probe (ping / rnpath / 0x01).
        Failure stamps poll_failed_at so status() can refuse the green face;
        success clears it (a proven-live path is direct evidence)."""
        rec = self.nodes.get(dst_hash)
        if rec is None:
            return None
        if ok:
            rec.poll_failed_at = None
        else:
            rec.poll_failed_at = now
        return rec

    # -- dashboard views ---------------------------------------------------

    def all(self, now: float) -> List[NodeRecord]:
        """Every node: the operator's OWN nodes first (kin above neighbours),
        alert-first within each group, then by name."""
        return sorted(
            (r for r in self.nodes.values() if not self._is_own_identity(r)),
            key=lambda r: (r.provenance != "kin",
                           _STATUS_RANK.get(r.status(now), 3), r.name.lower()))

    @_locked
    def probe_hash_for(self, key: str) -> Optional[str]:
        """A probeable 32-hex mesh destination for the DEVICE that record *key*
        belongs to, or None. A consolidated VITALS row can be LED by a non-hex
        key — e.g. an HTTP-discovery record keyed ``rtnode:<name>`` — while the
        same device also has a real hex mesh dest (its health-beacon aspect).
        'Ping node now' must probe the hex dest, not the unprobeable HTTP key, or
        rnpath errors and the node looks unreachable when it isn't. Groups the
        same way ``devices()`` does: by identity, else by (case-folded) name."""
        from monitor.mesh import is_hex_hash
        if is_hex_hash(key):
            return key                      # already a real dest — probe it directly
        rec = self.nodes.get(key)
        if rec is None:
            return None

        def _name(r) -> str:
            return name_key(getattr(r, "name", "") or
                            getattr(r, "announced_name", "") or "")

        ident = getattr(rec, "identity_hash", None)
        device = getattr(rec, "device_id", None)
        name = _name(rec)
        for h, r in self.nodes.items():
            if not is_hex_hash(h):
                continue
            if (ident and getattr(r, "identity_hash", None) == ident) or \
               (device and getattr(r, "device_id", None) == device) or \
               (name and _name(r) == name):
                return h                    # a real hex dest for the same device
        return None

    @_locked
    def _device_groups(self) -> List[List[NodeRecord]]:
        """Group every record into physical DEVICES: first by announced identity,
        then collapsing identity-groups that resolve to the same non-empty NAME.

        A single device can reach the medic three ways the registry CAN'T link by
        identity: its health-beacon dest, an HTTP /status keyed by node_name, and
        an rnpath path with no announce. Kin names are the operator's unique,
        authoritative labels, so a shared name is a safe merge; unnamed neighbours
        stay separate until an announce links them by identity. This is the single
        grouping both ``devices()`` (the dashboard) and ``consolidated_record()``
        (a tapped row's detail) share, so the merge and the display never diverge.

        Three passes, weakest evidence last: announced identity, then the roster's
        recorded device (what the medic saw with its own hands at birth), then a
        shared name.
        """
        groups: Dict[str, List[NodeRecord]] = {}
        for rec in self.nodes.values():
            # Never group/surface the medic's OWN destinations. An older
            # registry may already hold a polluted row (the 2026-08-22 lxmd
            # relay); filtering here cleans it up on the next render, no wipe
            # needed. Kin are a different identity and pass through untouched.
            if self._is_own_identity(rec):
                continue
            groups.setdefault(rec.identity_hash or rec.dst_hash, []).append(rec)

        def _collapse(key_of) -> None:
            """Merge groups that agree on *key_of*, ignoring groups with no key."""
            seen: Dict[str, str] = {}
            for key in list(groups.keys()):
                if key not in groups:            # already folded into another
                    continue
                value = key_of(groups[key])
                if not value:
                    continue
                target = seen.get(value)
                if target is not None and target != key:
                    groups[target].extend(groups.pop(key))
                else:
                    seen[value] = key

        # A DEVICE THE MEDIC BUILT IS ONE DEVICE, whatever it announces. A Pi
        # propagation node's health reporter keeps its own identity file, so its
        # beacon destination and its rnsd destination are two unrelated
        # identities and the pass above can never join them. The roster carries
        # what birth knew: these hashes are the same machine.
        _collapse(lambda members: next(
            (r.device_id for r in members if r.device_id), ""))

        # AN OPERATOR-SET KIN NAME IS AUTHORITATIVE AND UNIQUE — belt-and-
        # suspenders for the 2026-08-22 double-SkyFinger, catching any gap in
        # birth-time identity capture. The operator gives each of their OWN nodes
        # one unique name (they number them sequentially); they will not call two
        # different physical machines the same thing. So two groups that BOTH
        # carry the same operator name are one machine, and fold — EVEN when both
        # bear their own identity. That is the exact case the name pass below
        # refuses (rightly, for ANNOUNCED names, where a dead machine could hide
        # behind a live namesake — a name a stranger merely broadcasts is not
        # authority), so this runs first and is guarded to the operator's own
        # authority: the naming record must be in the KIN ROSTER (by dst or by
        # announced identity), which is the operator's own record of their fleet.
        # A bare mesh-heard row that merely wears a name is NOT roster-backed and
        # never triggers this — only ``rec.name`` (never announced_name) counts.
        def _operator_name(members) -> str:
            for r in members:
                if not r.name:
                    continue
                if (r.dst_hash in self.kin_roster
                        or (r.identity_hash
                            and r.identity_hash in self.kin_roster)):
                    return name_key(r.name)
            return ""
        _collapse(_operator_name)

        def _grp_name(members) -> str:
            p = sorted(members, key=lambda r: (r.provenance != "kin", not r.name))[0]
            return (p.name or p.announced_name or "").strip()

        # NAME IS THE WEAKEST JOIN, so it is the most guarded (break-lens,
        # 2026-08-13): two groups that each carry their OWN identity are two
        # machines whatever they are called — folding them let a dead machine
        # hide behind a live namesake, max(last_seen) painting the corpse
        # healthy. A name only pulls in rows that have no identity of their
        # own (the discovery placeholder, a bare heard-row).
        seen_names: Dict[str, str] = {}
        for key in list(groups.keys()):
            if key not in groups:
                continue
            value = name_key(_grp_name(groups[key]))
            if not value:
                continue
            target = seen_names.get(value)
            if target is not None and target != key and target in groups:
                a_ident = any(r.identity_hash for r in groups[target])
                b_ident = any(r.identity_hash for r in groups[key])
                if a_ident and b_ident:
                    continue                 # two machines; leave both visible
                groups[target].extend(groups.pop(key))
            else:
                seen_names[value] = key
        return list(groups.values())

    @staticmethod
    def _consolidate(members: List[NodeRecord], now: float) -> NodeRecord:
        """Fold a device's merged aspect-records into ONE record for display: its
        static identity (name/location/notes/log) from the best-known member, but
        its HEALTH (latest beacon, HTTP /status, mesh reachability, last-seen)
        POOLED from whichever members actually carry it — most recently heard wins.

        This is the health-side twin of ``probe_hash_for``: where that resolves a
        probeable mesh dest for a device led by a beacon-less record, this resolves
        the device's real decoded health. Without it, a row led by an HTTP
        ``rtnode:<name>`` record (no beacon) shows a green merged dot but a detail
        that reads 'No health beacon received yet' — the two disagree. Returns a
        shallow copy, so the stored records are never mutated."""
        import copy

        base = sorted(
            members,
            key=lambda r: (r.provenance != "kin", not r.name,
                           _STATUS_RANK.get(r.status(now), 3)))[0]
        merged = copy.copy(base)

        def _recency(r: NodeRecord) -> float:
            return r.last_seen if r.last_seen is not None else float("-inf")

        beacons = [r for r in members if r.latest_beacon is not None]
        if beacons:
            merged.latest_beacon = max(beacons, key=_recency).latest_beacon
        https = [r for r in members
                 if r.latest_http is not None and r.latest_http.reachable]
        if https:
            merged.latest_http = max(https, key=_recency).latest_http
        meshed = [r for r in members if r.mesh_hops is not None]
        if meshed:
            m = min(meshed, key=lambda r: r.mesh_hops)
            merged.mesh_hops = m.mesh_hops
            if not merged.mesh_interface:
                merged.mesh_interface = m.mesh_interface
        seen = [r.last_seen for r in members if r.last_seen is not None]
        if seen:
            merged.last_seen = max(seen)
        # Pooled like the health fields above: a device is a propagation
        # relay if ANY of its aspect-destinations announces as one — the
        # lxmd aspect must not lose the label just because a beacon-carrying
        # sibling led the merge.
        merged.is_propagation = any(r.is_propagation for r in members)
        # Echoes and direct words pool like sightings (freshest wins) but stay
        # in their own fields — a merged row must never let one aspect's replay
        # pass for another aspect's live word (the 2026-08-21 rule, device-
        # level), and the echo tag is GATED on the pooled direct word.
        echoes = [r.last_echo_at for r in members if r.last_echo_at is not None]
        if echoes:
            merged.last_echo_at = max(echoes)
        directs = [r.last_direct for r in members if r.last_direct is not None]
        if directs:
            merged.last_direct = max(directs)
        genuine = [r.last_heard_announce_at for r in members
                   if r.last_heard_announce_at is not None]
        if genuine:
            merged.last_heard_announce_at = max(genuine)
        return merged

    @_locked
    def consolidated_record(self, key: str, now: float) -> Optional[NodeRecord]:
        """The device-level health record for the row/record identified by *key*
        (any merged member's dst_hash). Returns a consolidated NodeRecord whose
        ``status(now)`` and ``latest_beacon`` reflect the WHOLE device — the same
        health the dashboard dot shows — so the node-detail hexagon and Health text
        agree with it. ``None`` if *key* is unknown."""
        if key not in self.nodes:
            return None
        for members in self._device_groups():
            if any(m.dst_hash == key for m in members):
                return self._consolidate(members, now)
        return None

    @_locked
    def devices(self, now: float) -> List[dict]:
        """The CONSOLIDATED dashboard: one row per physical device. Destinations
        that announced the same identity collapse into one entry (a phone's
        chat + files aspects are one phone), led by its best-known record.
        Each row adds ``aspects`` (how many destinations merged) and
        ``capabilities``: {lora, wifi, bluetooth, internet} — True (seen
        working), False (reported down), None (no way to know yet)."""
        out = []
        for members in self._device_groups():
            # Dot status/health come from the CONSOLIDATED record (health pooled
            # across members), not an arbitrary primary that may lack a beacon —
            # so the row and its tapped detail read the same device health.
            consolidated = self._consolidate(members, now)
            d = consolidated.to_dashboard(now)
            seen = [r.last_seen for r in members if r.last_seen is not None]
            if seen:
                d["last_seen_hours"] = max(0.0, (now - max(seen)) / 3600.0)
            d["aspects"] = len(members)
            d["capabilities"] = _capabilities(members)
            # "quiet" is recency-only (not the same as red): a device unheard past
            # QUIET_AFTER_HOURS sinks below the VITALS divider. A device never heard
            # at all (last_seen_hours 0.0) is not quiet — it just has nothing yet.
            d["quiet"] = bool(seen) and d["last_seen_hours"] > QUIET_AFTER_HOURS
            out.append(d)
        return sorted(out, key=lambda d: (d["provenance"] != "kin",
                                          _STATUS_RANK.get(d["status"], 3),
                                          d["name"].lower()))

    def activity(self, dst_hash: str, now: float,
                 tz_offset_hours: float = 0.0) -> dict:
        """A node's when-is-it-up profile (monitor.history.activity_profile over its
        heard-event series). The UI draws hour bars from ``by_hour`` and shows
        ``describe_activity`` beneath — most useful for intermittent nodes."""
        from monitor.history import activity_profile
        return activity_profile(self.history.series(dst_hash), now, tz_offset_hours)

    def located_nodes(self, now: float) -> List[dict]:
        """Every node with a known location, for SCAN mode — each as
        ``{lat, lon, name, status}``. Nodes without birth-cert coordinates are
        omitted (nothing to plot). Sorted by name for stable rendering."""
        out = []
        for rec in sorted(self.nodes.values(), key=lambda r: r.name.lower()):
            if self._is_own_identity(rec):
                continue                     # not a node on the map — it's us
            if rec.has_location():
                out.append({"lat": rec.lat, "lon": rec.lon,
                            "name": rec.name or "(unnamed)",
                            "status": rec.status(now)})
        return out

    def visible(self, now: float, status: Optional[str] = None,
                search: str = "") -> List[NodeRecord]:
        result = []
        for rec in self.all(now):
            if status and rec.status(now) != status:
                continue
            if search and search.lower() not in rec.name.lower():
                continue
            result.append(rec)
        return result

    @_locked
    def summary(self, now: float) -> Dict[str, int]:
        counts = {"ok": 0, "warn": 0, "alert": 0, "unknown": 0}
        for rec in self.nodes.values():
            if self._is_own_identity(rec):
                continue                     # the medic doesn't count itself
            counts[rec.status(now)] = counts.get(rec.status(now), 0) + 1
        return counts

    # -- commissioning log / field notes / firmware ------------------------

    def add_note(self, dst_hash: str, note: str, now: float,
                 operator: str = "operator") -> NodeRecord:
        rec = self.nodes.get(dst_hash) or self.register(dst_hash)
        rec.notes.append(note)
        rec.events.append(CommissionEvent(now, "note", note, operator))
        return rec

    def log_event(self, dst_hash: str, kind: str, summary: str, now: float,
                  operator: str = "operator") -> NodeRecord:
        rec = self.nodes.get(dst_hash) or self.register(dst_hash)
        rec.events.append(CommissionEvent(now, kind, summary, operator))
        return rec

    @_locked
    def nodes_needing_update(self, latest: str) -> List[NodeRecord]:
        return [r for r in self.nodes.values()
                if r.needs_firmware_update(latest)]

    # -- persistence (the monitoring DB MITOSIS copies) -------------

    @_locked
    def to_dict(self) -> dict:
        return {"nodes": [
            {
                "dst_hash": r.dst_hash,
                "name": r.name,
                "location": r.location,
                "node_type": r.node_type,
                "last_seen": r.last_seen,
                # Kept apart across a restart too. Without these the app comes
                # back unable to tell a route from having heard the node, and
                # the next mesh scan can overwrite fresh direct evidence with an
                # old path timestamp.
                "last_direct": r.last_direct,
                "mesh_heard": r.mesh_heard,
                "poll_failed_at": r.poll_failed_at,
                "last_echo_at": r.last_echo_at,
                "last_heard_announce_at": r.last_heard_announce_at,
                "last_announce_fp": r.last_announce_fp,
                "lat": r.lat,
                "lon": r.lon,
                "share_location": r.share_location,
                "share_applied_at": r.share_applied_at,
                "identity_hash": r.identity_hash,
                "announced_name": r.announced_name,
                "is_propagation": r.is_propagation,
                "notes": list(r.notes),
                "events": [
                    {"at": e.at, "kind": e.kind, "summary": e.summary,
                     "operator": e.operator}
                    for e in r.events
                ],
                "latest_beacon": (r.latest_beacon.to_bytes().hex()
                                  if r.latest_beacon else None),
            }
            for r in self.nodes.values()
        ], "history": self.history.to_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> "NodeRegistry":
        reg = cls()
        for n in data.get("nodes", []):
            rec = NodeRecord(
                dst_hash=n["dst_hash"],
                name=n.get("name", ""),
                location=n.get("location", ""),
                node_type=n.get("node_type", "rtnode2400"),
                last_seen=n.get("last_seen"),
                last_direct=n.get("last_direct"),
                mesh_heard=n.get("mesh_heard"),
                poll_failed_at=n.get("poll_failed_at"),
                last_echo_at=n.get("last_echo_at"),
                last_heard_announce_at=n.get("last_heard_announce_at"),
                last_announce_fp=n.get("last_announce_fp"),
                lat=n.get("lat"),
                lon=n.get("lon"),
                # A registry file written before this field existed carries no
                # answer, and no answer is HIDDEN — never "share".
                share_location=_share_policy(n.get("share_location")),
                share_applied_at=n.get("share_applied_at"),
                identity_hash=n.get("identity_hash"),
                # A registry saved before 2026-08-14 can carry decode residue
                # ("j(") as an announced name; the raw bytes are long gone, so
                # judge the string itself and drop what could never have been
                # meant as a name — the row heals to "Neighbour <hash8>".
                announced_name=(n.get("announced_name", "")
                                if _valid_display_name(
                                    n.get("announced_name", "")) else ""),
                is_propagation=bool(n.get("is_propagation", False)),
            )
            rec.notes = list(n.get("notes", []))
            rec.events = [CommissionEvent(**e) for e in n.get("events", [])]
            lb = n.get("latest_beacon")
            if lb:
                rec.latest_beacon = decode(bytes.fromhex(lb))
            reg.nodes[rec.dst_hash] = rec
        from monitor.history import NodeHistory
        reg.history = NodeHistory.from_dict(data.get("history", {}))
        return reg

    # -- disk persistence (so history/activity survives an app restart) --------

    @_locked
    def forget_node(self, name_or_hash: str) -> int:
        """Remove EVERYTHING this registry knows about one machine, so its
        name can be reused by a new birth (operator request, 2026-08-13).

        One machine leaves several rows: the build's roster placeholder
        ("rtnode:<name>"), plus one row per announced destination, tied
        together by identity_hash. Deleting only the row the operator tapped
        would leave siblings holding the name — and the reborn node would
        inherit a stranger's history, which is exactly the class of stale
        claim this tool exists not to make. So the match spreads: by name
        (case-insensitive), by the placeholder key, then across every row
        sharing an identity with anything already matched. History goes with
        the rows. Returns how many rows were removed; 0 is an honest answer
        for an unknown name, never an error.
        """
        want = (name_or_hash or "").strip()
        if not want:
            return 0
        low = want.lower()
        doomed = set()
        for h, rec in self.nodes.items():
            if ((rec.name or "").lower() == low or h == want
                    or h == f"rtnode:{low}"):
                doomed.add(h)
        idents = {self.nodes[h].identity_hash
                  for h in doomed if self.nodes[h].identity_hash}
        for h, rec in self.nodes.items():
            if rec.identity_hash and rec.identity_hash in idents:
                doomed.add(h)
        for h in doomed:
            self.nodes.pop(h, None)
            try:
                self.history._series.pop(h, None)
            except AttributeError:
                pass
        return len(doomed)

    def save(self, path: str) -> bool:
        """Atomically persist the registry (nodes + history) to *path* as JSON, so
        the heard-event / activity series accumulates ACROSS sessions instead of
        resetting on every restart. Best-effort — returns False on any error and
        never raises; the temp-file + rename keeps a crash from leaving a half file."""
        import json
        import os
        import tempfile
        tmp = None
        try:
            path = os.path.expanduser(path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            from monitor.atomic_json import write_json
            tmp = None                                # helper manages its own
            return write_json(path, self.to_dict())   # fsync + atomic rename
        except Exception:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False

    @classmethod
    def load(cls, path: str) -> "NodeRegistry":
        """Load a saved registry, or a fresh empty one if the file is missing or
        unreadable (never raises — a corrupt file just starts clean)."""
        import json
        import os
        try:
            with open(os.path.expanduser(path)) as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return cls()
