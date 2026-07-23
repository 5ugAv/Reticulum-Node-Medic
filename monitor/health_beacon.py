"""Type B (RTNode-2400) health-beacon codec — receive-side contract.

RTNode-2400 nodes cannot run LXMF (their embedded C++ Reticulum has core RNS
only). Instead they carry health in the ``app_data`` of a periodic RNS
*announce* on a dedicated health aspect. The tool registers an announce
handler for that aspect and decodes this compact 14-byte, big-endian payload.
The node's identity **is** the announce source hash — no node id is in the
payload; the tool maps the destination hash to a node profile in its registry
(name/location/GPS were set at build time on the "birth certificate").

Wire layout (all big-endian):
    [0]      format version (0x01)
    [1..4]   uptime seconds            (uint32)
    [5..6]   free heap KB              (uint16)   # low-water mark preferred
    [7]      WiFi RSSI dBm             (int8; 0 when WiFi down)
    [8]      reset reason              (enum, see RESET_REASONS)
    [9]      flags                     (bit0 wifi_up, bit1 lora_up,
                                        bit2 tcp_backbone_up,
                                        bit3 local_tcp_server_up,
                                        bit4 wdt_armed, bit5 psram,
                                        bit6 fault/breach, bit7 reserved)
    [10]     board id                  (0x3F = Heltec V4)
    [11..13] firmware version major, minor, patch

Newer format versions may append bytes; decode() reads only the v1 prefix so
old and new tools interoperate.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

PAYLOAD_LEN = 14
FORMAT_VERSION = 0x01

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
    0x4B: "T-Watch S3 Plus",
    0x50: "Generic nRF52",
    0x51: "RAK4631",
    0x52: "XIAO nRF",
}

# WiFi RSSI thresholds (dBm) for RTNode-2400 nodes. Weak WiFi is only ever a
# WARN, never an ALERT: a healthy node (no faults, LoRa up) that merely
# associates at a weak RSSI must not go red. WIFI_ALERT_DBM is retained as the
# "very weak" boundary but no longer escalates to alert on its own.
WIFI_WARN_DBM = -75
WIFI_ALERT_DBM = -85


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

    @property
    def reset_reason_label(self) -> str:
        return RESET_REASONS.get(self.reset_reason, "unknown")

    @property
    def board_label(self) -> str:
        return BOARD_IDS.get(self.board_id, f"unknown(0x{self.board_id:02x})")

    def to_bytes(self) -> bytes:
        """Re-encode to the 14-byte wire payload (inverse of decode)."""
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
            fw=fw, format_version=self.format_version)


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
) -> bytes:
    """Reference encoder — mirrors what the firmware announcer must emit."""
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
    return struct.pack(
        ">BIHbBBBBBB",
        format_version,
        uptime_s & 0xFFFFFFFF,
        heap_kb & 0xFFFF,
        max(-128, min(127, wifi_rssi_dbm)),
        reset_reason & 0xFF,
        flags & 0xFF,
        board_id & 0xFF,
        fw[0] & 0xFF, fw[1] & 0xFF, fw[2] & 0xFF,
    )


def decode(app_data: bytes) -> HealthBeacon:
    """Decode a beacon payload. Extra trailing bytes (future versions) are
    ignored so a v1 tool still reads a v2 beacon's shared prefix."""
    if len(app_data) < PAYLOAD_LEN:
        raise ValueError(
            f"health beacon too short: {len(app_data)} < {PAYLOAD_LEN} bytes")
    (version, uptime, heap, rssi, reset, flags, board,
     fw_major, fw_minor, fw_patch) = struct.unpack_from(">BIHbBBBBBB", app_data, 0)
    return HealthBeacon(
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


def beacon_status(b: HealthBeacon) -> str:
    """Map a beacon to a Monitor status colour: ok / warn / alert.

    RED (alert) is reserved for real problems: a fault flag or LoRa down. Weak
    WiFi RSSI only ever escalates to WARN (orange) — never alert — so a healthy
    node associating on a weak link does not false-alarm.
    """
    if b.fault or not b.lora_up:
        return "alert"
    status = "ok"
    if b.wifi_up and b.wifi_rssi_dbm <= WIFI_WARN_DBM:
        status = "warn"
    if not b.wdt_armed and status == "ok":
        status = "warn"
    return status
