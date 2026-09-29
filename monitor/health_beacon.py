"""Type B (RTNode-2400) health-beacon codec — receive-side contract.

RTNode-2400 nodes cannot run LXMF (their embedded C++ Reticulum has core RNS
only). Instead they carry health in the ``app_data`` of a periodic RNS
*announce* on a dedicated health aspect. The tool registers an announce
handler for that aspect and decodes this compact 14-byte, big-endian payload.
The node's identity **is** the announce source hash — no node id is in the
payload; the tool maps the destination hash to a node profile in its registry
(name/location/GPS were set at build time on the "birth certificate").

Wire layout (all big-endian):
    [0]      format version (0x01 = v1, 0x02 = v2 with power+link tail)
    [1..4]   uptime seconds            (uint32)
    [5..6]   free heap KB              (uint16)   # low-water mark preferred
    [7]      WiFi RSSI dBm             (int8; 0 when WiFi down)
    [8]      reset reason              (enum, see RESET_REASONS)
    [9]      flags                     (bit0 wifi_up, bit1 lora_up,
                                        bit2 tcp_backbone_up,
                                        bit3 local_tcp_server_up,
                                        bit4 wdt_armed, bit5 psram,
                                        bit6 fault/breach, bit7 airtime_lock)
    [10]     board id                  (0x3F = Heltec V4)
    [11..13] firmware version major, minor, patch
    -- v2 tail (present only when format version >= 0x02) --------------------
    [14..15] battery millivolts        (uint16; 0 = no/unknown battery)
    [16]     battery percent           (uint8; 0xFF = unknown)
    [17]     power flags               (bit0 on_battery, bit1 charging,
                                        bit2 on_solar, bit3 on_mains)
    [18]     LoRa link SNR dB          (int8; -128 = unknown) — node's view
    [19]     LoRa link RSSI dBm        (int8; -128 = unknown) — node's view

Newer format versions may append bytes; decode() reads only the prefix a given
version defines, so old and new tools interoperate. A v1 tool reading a v2
beacon simply ignores the power+link tail; a v2 tool reading a v1 beacon leaves
the power/link fields None.

The v2 tail exists so *every* birthed node — RTNode-2400 (VBAT pin) and Pi+RNode
propagation (UPS/INA219) — can report battery + transmission state, which is
what lets VITALS say whether a node is orange because of battery or because of
its link.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

PAYLOAD_LEN = 14           # v1 payload / shared prefix length
PAYLOAD_LEN_V2 = 20        # v1 prefix (14) + power+link tail (6)
PAYLOAD_LEN_V3 = 29        # v2 (20) + position tail (9)
PAYLOAD_LEN_V4_HEAD = 30   # v3 (29) + the neighbour count byte
FORMAT_VERSION = 0x01
FORMAT_VERSION_V2 = 0x02
FORMAT_VERSION_V3 = 0x03   # + self-reported position (GPS-capable nodes)
FORMAT_VERSION_V4 = 0x04   # + who this node can hear (2026-09-29)

# --- v4: WHO THIS NODE CAN HEAR ---------------------------------------------
#
# The map could only ever draw what the MEDIC hears. Every edge in its topology
# starts at the medic, because the medic's path table is the only evidence it
# has — so two nodes that talk to each other all day are drawn as two unrelated
# dots. The operator asked for the missing half: "the nodes will report back to
# Medic which other nodes they're in contact with."
#
# They also asked whether overlapping range-test rings could stand in for it.
# They cannot, and the reason is worth keeping here: a range test measures how
# far the MEDIC, on foot, could walk from a node while still hearing it. That
# is a node-to-handheld reach in the directions somebody actually walked. B may
# be on a roof with a better antenna, or behind a hill on a bearing nobody
# walked. Treating that as a link puts an INFERRED edge on the map wearing the
# same clothes as an observed one, which is the failure this project keeps
# digging out. Rings are a fine hint; they are not evidence.
#
# BYTE BUDGET, because LoRa airtime is the scarcest shared thing we spend:
# one count byte, then 4 bytes per neighbour —
#
#   uint16  short hash   the first 2 bytes of the neighbour's dest hash
#   int8    best SNR     dB, LORA_LINK_UNKNOWN when not measured
#   uint8   age bucket   how long ago it was last heard (see AGE_BUCKETS_S)
#
# Capped at NEIGHBOURS_MAX, so the tail can never exceed 1 + 6*4 = 25 bytes on
# a beacon that is already ~29. A node with more neighbours sends its best
# ones: freshest first, then strongest.
NEIGHBOUR_LEN = 4
NEIGHBOURS_MAX = 6
NEIGHBOUR_HASH_LEN = 2

#: Age buckets, in seconds, for the 1-byte "when did you last hear it" field.
#: Buckets rather than a timestamp: the medic only needs to know whether a
#: claim is fresh enough to draw, and a bucket costs 1 byte where an epoch
#: costs 4. The index is what travels; the value is the bucket's UPPER bound.
AGE_BUCKETS_S = (60, 300, 900, 3600, 10800, 43200, 172800)
#: Anything older than the last bucket, or never heard: do not draw it.
AGE_BUCKET_STALE = len(AGE_BUCKETS_S)

#: v2 tail sentinels for "field not reported".
BATTERY_MV_UNKNOWN = 0
BATTERY_PCT_UNKNOWN = 0xFF
LORA_LINK_UNKNOWN = -128   # int8 sentinel for snr/rssi (below any real value)

#: v3 position tail: int32 microdegrees; this sentinel = "no fix / not
#: reported" (outside the valid ±180e6 range so it can never be a place).
POSITION_UNKNOWN = 0x7FFFFFFF

#: Battery charge thresholds (percent), used only when the node reports battery
#: AND is not charging: a discharging node running down is what we flag. A
#: charging / mains node low on charge is recovering, so it never reds on this.
BATTERY_WARN_PCT = 30
BATTERY_ALERT_PCT = 12

#: LoRa link SNR (dB) below which the node's own link reads marginal. Like weak
#: WiFi, a weak-but-present LoRa link is only ever a WARN, never an alert — the
#: node is still reachable, it just wants a better antenna/placement.
LORA_SNR_WARN_DB = -9

#: RNS announce aspect the tool listens on (app_name.aspect). Both sides must
#: construct the Destination with exactly this app_name + aspects or the
#: destination hashes will not match.
ANNOUNCE_ASPECT = "rtnode.health"

RESET_REASONS = {
    0: "poweron",
    1: "panic",
    2: "brownout",
    3: "task_wdt",
    4: "sw",
    5: "other",
}

# Board id byte == RNode firmware BOARD_MODEL (this is a 5ugAv RNode fork, so
# the tool and firmware share one enum). RTNode-2400 target is 0x3F.
BOARD_IDS = {
    0x31: "RNode v1",
    0x32: "HMBRW",
    0x33: "T-Beam",
    0x34: "Huzzah32",
    0x35: "Generic ESP32",
    0x36: "LoRa32 v2.0",
    0x37: "LoRa32 v2.1",
    0x38: "Heltec32 V2",
    0x39: "LoRa32 v1.0",
    0x3A: "Heltec32 V3",
    0x3B: "T-Deck",
    0x3C: "Heltec T114",
    0x3D: "T-Beam S v1",
    0x3E: "XIAO S3",
    0x3F: "Heltec32 V4",
    0x40: "RNode NG 2.0",
    0x41: "RNode NG 2.1",
    0x42: "T3S3",
    0x44: "T-Echo",
    # 0x47 is the Ebyte EoRa-S3 (EoRa PI family), added 2026-09-08 when
    # it became an RTNode-2400 target. Without it verify_beacon reports a
    # successful birth as "Board is beaconing: unknown(0x47)".
    0x47: "Ebyte EoRa-S3",
    0x4B: "T-Watch S3 Plus",
    0x50: "Generic nRF52",
    0x51: "RAK4631",
    0x52: "XIAO nRF",
    # Synthetic ids for node types that are NOT an RNode board but still emit a
    # health beacon (they run full RNS in Python, not the C++ firmware). Kept
    # above the RNode range (0x31..0x52) so they never collide with a real board.
    0xA0: "RPi propagation",
}

#: Synthetic board id a Pi+RNode propagation node reports (see BOARD_IDS 0xA0).
BOARD_PI_PROPAGATION = 0xA0

# WiFi RSSI thresholds (dBm) for RTNode-2400 nodes. Weak WiFi is only ever a
# WARN, never an ALERT: a healthy node (no faults, LoRa up) that merely
# associates at a weak RSSI must not go red. WIFI_ALERT_DBM is retained as the
# "very weak" boundary but no longer escalates to alert on its own.
WIFI_WARN_DBM = -75
WIFI_ALERT_DBM = -85


def age_bucket(age_s) -> int:
    """Seconds-since-last-heard -> the 1-byte bucket index that travels.

    Returns AGE_BUCKET_STALE for None, for a negative age (a clock that went
    backwards is not evidence of freshness), and for anything past the last
    bucket.
    """
    if age_s is None:
        return AGE_BUCKET_STALE
    try:
        age = float(age_s)
    except (TypeError, ValueError):
        return AGE_BUCKET_STALE
    if age < 0:
        return AGE_BUCKET_STALE
    for i, upper in enumerate(AGE_BUCKETS_S):
        if age <= upper:
            return i
    return AGE_BUCKET_STALE


def bucket_seconds(index) -> Optional[int]:
    """A bucket index back to its UPPER bound in seconds, or None when the
    bucket says stale. Upper bound, never a midpoint: the honest reading of
    "bucket 2" is "no older than 900 s", and the medic must not invent a
    precision the node never sent."""
    if not isinstance(index, int) or index < 0 or index >= len(AGE_BUCKETS_S):
        return None
    return AGE_BUCKETS_S[index]


def short_hash(dest_hash) -> Optional[int]:
    """The 2 bytes of a destination hash that travel in a neighbour entry.

    Two bytes is 65536 buckets, which WILL collide on a big enough mesh — so
    the medic treats this as a POINTER to a node it already knows, never as an
    identity. encode/decode keep it dumb; monitor.neighbours owns the matching
    and what to do when two known nodes share a prefix.
    """
    if dest_hash is None:
        return None
    if isinstance(dest_hash, int):
        return dest_hash & 0xFFFF
    if isinstance(dest_hash, str):
        try:
            dest_hash = bytes.fromhex(dest_hash)
        except ValueError:
            return None
    if not isinstance(dest_hash, (bytes, bytearray)) or len(dest_hash) < NEIGHBOUR_HASH_LEN:
        return None
    return int.from_bytes(bytes(dest_hash[:NEIGHBOUR_HASH_LEN]), "big")


def pack_neighbours(neighbours) -> bytes:
    """``[(dest_hash, snr_db, age_s), ...]`` -> the v4 tail.

    Freshest first, then strongest, then capped at NEIGHBOURS_MAX: when a node
    hears more than it can afford to report, the ones worth the airtime are the
    ones it heard recently and heard well. Entries with no usable hash are
    dropped rather than sent as zeros — a zero hash would match a real node.
    """
    rows = []
    for entry in neighbours or ():
        try:
            dest, snr, age = entry
        except (TypeError, ValueError):
            continue
        h = short_hash(dest)
        if h is None:
            continue
        bucket = age_bucket(age)
        if bucket >= AGE_BUCKET_STALE:
            continue                      # nothing to say about it
        if snr is None:
            snr_b = LORA_LINK_UNKNOWN
        else:
            snr_b = max(-127, min(127, int(round(float(snr)))))
        rows.append((bucket, -(snr_b if snr_b != LORA_LINK_UNKNOWN else -128), h, snr_b))
    rows.sort(key=lambda r: (r[0], r[1]))
    rows = rows[:NEIGHBOURS_MAX]
    out = bytearray([len(rows)])
    for bucket, _rank, h, snr_b in rows:
        out += struct.pack(">HbB", h, snr_b, bucket)
    return bytes(out)


def unpack_neighbours(app_data: bytes):
    """The v4 tail -> ``[{"short_hash", "snr_db", "age_s"}, ...]``.

    Length-gated like every other tail: a truncated payload yields the entries
    that are wholly present and drops the rest, rather than over-reading or
    raising. A beacon that claims 6 neighbours and carries 2 is a damaged
    beacon, not a reason to lose the 2.
    """
    if len(app_data) < PAYLOAD_LEN_V4_HEAD:
        return []
    count = app_data[PAYLOAD_LEN_V3]
    out = []
    for i in range(min(count, NEIGHBOURS_MAX)):
        off = PAYLOAD_LEN_V4_HEAD + i * NEIGHBOUR_LEN
        if off + NEIGHBOUR_LEN > len(app_data):
            break
        h, snr, bucket = struct.unpack_from(">HbB", app_data, off)
        out.append({
            "short_hash": h,
            "snr_db": None if snr == LORA_LINK_UNKNOWN else snr,
            "age_s": bucket_seconds(bucket),
        })
    return out


@dataclass
class HealthBeacon:
    format_version: int
    uptime_s: int
    free_heap_kb: int
    wifi_rssi_dbm: int
    reset_reason: int
    wifi_up: bool
    lora_up: bool
    tcp_backbone_up: bool
    local_tcp_server_up: bool
    wdt_armed: bool
    psram: bool
    fault: bool            # b6: internal-SRAM low-water below early-warning
    airtime_lock: bool     # b7: LoRa duty-cycle limiter engaged
    board_id: int
    firmware_version: str

    # -- v2 power + link tail (None/False when the beacon is v1) -------------
    battery_mv: Optional[int] = None       # battery voltage, millivolts
    battery_pct: Optional[int] = None      # charge estimate 0..100
    #: None = the beacon predates the BT bits (unknown); True/False = the
    #: node's own verdict (power-flags byte, KNOWN 0x10 / UP 0x20 pair).
    bt_up: Optional[bool] = None
    on_battery: bool = False               # running from battery (not external)
    charging: bool = False                 # battery is charging
    on_solar: bool = False                 # solar input present
    on_mains: bool = False                 # wall/DC external input present
    lora_snr_db: Optional[int] = None      # node's last-heard link SNR
    lora_rssi_dbm: Optional[int] = None    # node's last-heard link RSSI

    # -- v3 position tail (None when the beacon predates it or has no fix) --
    #: Self-reported position, degrees. THE NODE'S OWN CLAIM about where it
    #: stands (live GNSS), distinct from the birth-certificate stamp. A
    #: fuzzed position is deliberately imprecise (wild-node privacy) and
    #: must never be presented as exact.
    lat: Optional[float] = None
    lng: Optional[float] = None
    position_sats: Optional[int] = None
    position_fuzzed: bool = False

    # -- v4 neighbours tail: who this node can hear -------------------------
    #: ``[{"short_hash", "snr_db", "age_s"}, ...]`` — empty on any beacon older
    #: than v4, which is not the same as "hears nobody". A node that has never
    #: reported neighbours must never be DRAWN as isolated; the medic's
    #: topology only adds edges, it never removes one for silence here.
    neighbours: list = field(default_factory=list)

    @property
    def reset_reason_label(self) -> str:
        return RESET_REASONS.get(self.reset_reason, "unknown")

    @property
    def board_label(self) -> str:
        return BOARD_IDS.get(self.board_id, f"unknown(0x{self.board_id:02x})")

    @property
    def has_power_telemetry(self) -> bool:
        """True once the node reports any battery/power reading (a v2 beacon)."""
        return self.battery_pct is not None or self.battery_mv is not None

    @property
    def has_link_telemetry(self) -> bool:
        """True once the node reports its own LoRa link quality (a v2 beacon)."""
        return self.lora_snr_db is not None or self.lora_rssi_dbm is not None

    @property
    def has_position(self) -> bool:
        """True when the node self-reported a live position (a v3 beacon
        with a fix — a v3 beacon WITHOUT a fix reports honestly nothing)."""
        return self.lat is not None and self.lng is not None

    @property
    def power_source_label(self) -> str:
        """Plain-English power source for the node-detail panel."""
        if self.on_solar:
            return "solar" + (" (charging)" if self.charging else "")
        if self.on_mains:
            return "mains" + (" (charging)" if self.charging else "")
        if self.on_battery:
            return "battery" + (" (charging)" if self.charging else "")
        return "unknown"

    @property
    def battery_label(self) -> str:
        """e.g. '78%  (3.94 V)' or 'not reported'."""
        if not self.has_power_telemetry:
            return "not reported"
        bits = []
        if self.battery_pct is not None:
            bits.append(f"{self.battery_pct}%")
        if self.battery_mv:
            bits.append(f"{self.battery_mv / 1000:.2f} V")
        return "  ".join(bits) if bits else "not reported"

    def to_bytes(self) -> bytes:
        """Re-encode to the wire payload (inverse of decode). Emits v2 (20 bytes)
        when the beacon carries power/link telemetry, else v1 (14 bytes)."""
        parts = (self.firmware_version.split(".") + ["0", "0", "0"])[:3]
        fw = tuple(int(p) if p.isdigit() else 0 for p in parts)
        return encode(
            self.uptime_s, self.free_heap_kb, self.wifi_rssi_dbm,
            self.reset_reason,
            wifi_up=self.wifi_up, lora_up=self.lora_up,
            tcp_backbone_up=self.tcp_backbone_up,
            local_tcp_server_up=self.local_tcp_server_up,
            wdt_armed=self.wdt_armed, psram=self.psram, fault=self.fault,
            board_id=self.board_id, airtime_lock=self.airtime_lock,
            fw=fw, format_version=self.format_version,
            battery_mv=self.battery_mv, battery_pct=self.battery_pct,
            bt_up=self.bt_up,
            on_battery=self.on_battery, charging=self.charging,
            on_solar=self.on_solar, on_mains=self.on_mains,
            lora_snr_db=self.lora_snr_db, lora_rssi_dbm=self.lora_rssi_dbm,
            lat=self.lat, lng=self.lng, position_sats=self.position_sats,
            position_fuzzed=self.position_fuzzed)


def encode(
    uptime_s: int,
    heap_kb: int,
    wifi_rssi_dbm: int,
    reset_reason: int,
    *,
    wifi_up: bool,
    lora_up: bool,
    tcp_backbone_up: bool,
    local_tcp_server_up: bool,
    wdt_armed: bool,
    psram: bool,
    fault: bool,
    board_id: int,
    airtime_lock: bool = False,
    fw=(0, 0, 0),
    format_version: int = FORMAT_VERSION,
    battery_mv: Optional[int] = None,
    battery_pct: Optional[int] = None,
    bt_up: Optional[bool] = None,
    on_battery: bool = False,
    charging: bool = False,
    on_solar: bool = False,
    on_mains: bool = False,
    lora_snr_db: Optional[int] = None,
    lora_rssi_dbm: Optional[int] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    neighbours=None,
    position_sats: Optional[int] = None,
    position_fuzzed: bool = False,
) -> bytes:
    """Reference encoder — mirrors what the firmware announcer must emit.

    Emits a v2 payload (20 bytes, power+link tail) when any power/link telemetry
    is supplied or ``format_version`` is >= 2; otherwise a plain v1 payload.
    """
    flags = (
        (0x01 if wifi_up else 0)
        | (0x02 if lora_up else 0)
        | (0x04 if tcp_backbone_up else 0)
        | (0x08 if local_tcp_server_up else 0)
        | (0x10 if wdt_armed else 0)
        | (0x20 if psram else 0)
        | (0x40 if fault else 0)
        | (0x80 if airtime_lock else 0)
    )
    has_pos = (format_version >= FORMAT_VERSION_V3
               or (lat is not None and lng is not None)
               or position_sats is not None)
    has_tail = (
        has_pos
        or format_version >= FORMAT_VERSION_V2
        or battery_mv is not None or battery_pct is not None
        or on_battery or charging or on_solar or on_mains
        or lora_snr_db is not None or lora_rssi_dbm is not None
    )
    version = (FORMAT_VERSION_V3 if has_pos
               else FORMAT_VERSION_V2 if has_tail else format_version)
    head = struct.pack(
        ">BIHbBBBBBB",
        version,
        uptime_s & 0xFFFFFFFF,
        heap_kb & 0xFFFF,
        max(-128, min(127, wifi_rssi_dbm)),
        reset_reason & 0xFF,
        flags & 0xFF,
        board_id & 0xFF,
        fw[0] & 0xFF, fw[1] & 0xFF, fw[2] & 0xFF,
    )
    if not has_tail:
        return head
    power_flags = (
        (0x10 if bt_up is not None else 0)
        | (0x20 if bt_up else 0)
        | (0x01 if on_battery else 0)
        | (0x02 if charging else 0)
        | (0x04 if on_solar else 0)
        | (0x08 if on_mains else 0)
    )
    tail = struct.pack(
        ">HBBbb",
        (BATTERY_MV_UNKNOWN if battery_mv is None else max(0, min(0xFFFF, battery_mv))),
        (BATTERY_PCT_UNKNOWN if battery_pct is None else max(0, min(100, battery_pct))),
        power_flags & 0xFF,
        (LORA_LINK_UNKNOWN if lora_snr_db is None else max(-127, min(127, lora_snr_db))),
        (LORA_LINK_UNKNOWN if lora_rssi_dbm is None else max(-127, min(127, lora_rssi_dbm))),
    )
    if not has_pos:
        return head + tail
    # v3 position tail: int32 microdegrees + [flags|sats] byte. A v3 beacon
    # with no fix carries the sentinel — "I can know my position but don't
    # right now" is itself information (GPS fitted, hunting sky).
    if lat is not None and lng is not None:
        lat_u = max(-90_000_000, min(90_000_000, int(round(lat * 1e6))))
        lng_u = max(-180_000_000, min(180_000_000, int(round(lng * 1e6))))
        pflags = 0x01 | (0x02 if position_fuzzed else 0)
    else:
        lat_u = lng_u = POSITION_UNKNOWN
        pflags = 0
    pflags |= (min(63, position_sats or 0) << 2)
    pos = struct.pack(">iiB", lat_u, lng_u, pflags & 0xFF)
    # v4 tail only when the node has something to say AND declares the version.
    # A v4 beacon with an empty list still carries its count byte (0), which is
    # how "asked and heard nobody" differs from "too old to be asked".
    if format_version >= FORMAT_VERSION_V4:
        return head + tail + pos + pack_neighbours(neighbours)
    return head + tail + pos


def decode(app_data: bytes) -> HealthBeacon:
    """Decode a beacon payload. Extra trailing bytes (future versions) are
    ignored so a v1 tool still reads a v2 beacon's shared prefix."""
    if len(app_data) < PAYLOAD_LEN:
        raise ValueError(
            f"health beacon too short: {len(app_data)} < {PAYLOAD_LEN} bytes")
    # THE FIRST BYTE IS A FORMAT VERSION, AND IT IS ENFORCED. Without this,
    # anything >= 14 bytes decoded — so every LXMF phone announce on the mesh
    # (msgpack, first byte 0x9x) landed on VITALS as a red-alerting phantom
    # node (seen live on the operator's screen, 2026-08-13). The ceiling of
    # 0x0F rejects text and msgpack while leaving room for future versions,
    # whose shared prefix still decodes below.
    if not (0x01 <= app_data[0] <= 0x0F):
        raise ValueError(
            f"not a health beacon: format byte 0x{app_data[0]:02x}")
    (version, uptime, heap, rssi, reset, flags, board,
     fw_major, fw_minor, fw_patch) = struct.unpack_from(">BIHbBBBBBB", app_data, 0)
    b = HealthBeacon(
        format_version=version,
        uptime_s=uptime,
        free_heap_kb=heap,
        wifi_rssi_dbm=rssi,
        reset_reason=reset,
        wifi_up=bool(flags & 0x01),
        lora_up=bool(flags & 0x02),
        tcp_backbone_up=bool(flags & 0x04),
        local_tcp_server_up=bool(flags & 0x08),
        wdt_armed=bool(flags & 0x10),
        psram=bool(flags & 0x20),
        fault=bool(flags & 0x40),
        airtime_lock=bool(flags & 0x80),
        board_id=board,
        firmware_version=f"{fw_major}.{fw_minor}.{fw_patch}",
    )
    # v2 power+link tail — read when the beacon is long enough. Gated on length
    # (not just the version byte) so a truncated payload can never over-read.
    if len(app_data) >= PAYLOAD_LEN_V2:
        batt_mv, batt_pct, power_flags, snr, rssi_lora = struct.unpack_from(
            ">HBBbb", app_data, PAYLOAD_LEN)
        b.battery_mv = None if batt_mv == BATTERY_MV_UNKNOWN else batt_mv
        b.battery_pct = None if batt_pct == BATTERY_PCT_UNKNOWN else batt_pct
        b.bt_up = bool(power_flags & 0x20) if (power_flags & 0x10) else None
        b.on_battery = bool(power_flags & 0x01)
        b.charging = bool(power_flags & 0x02)
        b.on_solar = bool(power_flags & 0x04)
        b.on_mains = bool(power_flags & 0x08)
        b.lora_snr_db = None if snr == LORA_LINK_UNKNOWN else snr
        b.lora_rssi_dbm = None if rssi_lora == LORA_LINK_UNKNOWN else rssi_lora
    # v3 position tail — length-gated like the v2 tail, and presence-gated on
    # the flags bit AND believable coordinates, so garbage can never become a
    # map pin.
    if len(app_data) >= PAYLOAD_LEN_V3:
        lat_u, lng_u, pflags = struct.unpack_from(">iiB", app_data, PAYLOAD_LEN_V2)
        sats = (pflags >> 2) & 0x3F
        b.position_sats = sats if sats else None
        if (pflags & 0x01 and lat_u != POSITION_UNKNOWN
                and lng_u != POSITION_UNKNOWN
                and -90_000_000 <= lat_u <= 90_000_000
                and -180_000_000 <= lng_u <= 180_000_000):
            b.lat = lat_u / 1e6
            b.lng = lng_u / 1e6
            b.position_fuzzed = bool(pflags & 0x02)
    # v4 neighbours tail — length-gated like the rest, so a v3 beacon simply
    # has none and a truncated v4 yields the entries that are wholly present.
    if len(app_data) >= PAYLOAD_LEN_V4_HEAD:
        b.neighbours = unpack_neighbours(app_data)
    return b


def battery_reason(b: HealthBeacon) -> Optional[str]:
    """Battery's contribution to status: 'alert', 'warn', or None.

    Only a *discharging* battery running down flags — a charging or mains node
    low on charge is recovering, and a solar node dipping overnight is normal
    (the outage watch, not the instant colour, decides if it truly died).
    """
    if b.battery_pct is None:
        return None
    if b.charging or (b.on_mains and not b.on_battery):
        return None
    if b.battery_pct <= BATTERY_ALERT_PCT:
        return "alert"
    if b.battery_pct <= BATTERY_WARN_PCT:
        return "warn"
    return None


def link_reason(b: HealthBeacon) -> Optional[str]:
    """The node's own LoRa link contribution: 'warn' for a marginal link, else
    None. A weak-but-present link is never an alert (still reachable)."""
    if b.lora_snr_db is not None and b.lora_snr_db <= LORA_SNR_WARN_DB:
        return "warn"
    return None


def beacon_status(b: HealthBeacon) -> str:
    """Map a beacon to a Monitor status colour: ok / warn / alert.

    RED (alert) is reserved for real problems: a fault flag, LoRa down, or a
    battery critically low *and discharging*. Weak WiFi/LoRa link and a merely
    low (still-charging or draining-but-not-critical) battery are WARN (orange)
    — never alert — so a healthy node does not false-alarm.
    """
    if b.fault or not b.lora_up or battery_reason(b) == "alert":
        return "alert"
    status = "ok"
    if b.wifi_up and b.wifi_rssi_dbm <= WIFI_WARN_DBM:
        status = "warn"
    if not b.wdt_armed and status == "ok":
        status = "warn"
    if status == "ok" and (battery_reason(b) == "warn" or link_reason(b) == "warn"):
        status = "warn"
    return status
