"""Health reporter for a **Pi + RNode propagation node** (the EVERYWHERE class).

RTNode-2400 nodes report health from their C++ firmware; a Pi propagation node
had **no health path at all** — it was born (RNS/LXMF + a flashed RNode) but
never told the medic how it was doing, so it always showed grey. This closes
that gap: a Pi node runs full RNS in Python, so it can build the *same* health
beacon the medic already decodes (:mod:`monitor.health_beacon`) and announce it
on the ``rtnode.health`` aspect — battery + transmission included (the v2 tail),
which is exactly what the operator needs to know whether a solar node is orange
because of its battery or its link.

Design mirrors :mod:`monitor.ups`: a PURE core (``collect_pi_health`` + the
``/proc`` parsers) that takes injected readings and is fully unit-testable with
no hardware, and a thin transport layer (reading the real OS values, and the RNS
announce loop) whose imports are lazy so this module stays importable in CI with
neither RNS nor an I2C bus.

Bandwidth ethos: this transmits on shared LoRa airtime, so it announces on the
same slow cadence gradient as everything else (see ``beacon-cadence-plan``) — a
periodic heartbeat, plus an immediate beacon when the medic commands one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from monitor.health_beacon import (
    HealthBeacon,
    BOARD_PI_PROPAGATION,
    FORMAT_VERSION_V2,
)

#: Disk usage (percent) at/above which the node flags a fault — a propagation
#: node that fills its SD card stops storing/forwarding and risks corruption.
DISK_FAULT_PCT = 95

#: free_heap_kb is a uint16 (max 65535 KB ≈ 64 MB). A Pi has far more RAM than
#: that, so we cap it: the field only carries signal when RAM is actually low
#: (matches the firmware's "free heap low-water" intent — high = plenty).
FREE_KB_CAP = 0xFFFF


@dataclass
class PiHealthInputs:
    """Everything ``collect_pi_health`` needs, all injected so the builder is
    pure. The runtime fills these from the OS / UPS / RNS; tests pass literals."""
    uptime_s: int
    free_ram_kb: int
    disk_used_pct: int
    net_up: bool                 # has an IP network (wifi or ethernet)
    radio_up: bool               # the attached RNode is present/responding
    rns_transport_up: bool       # RNS has a live interface to the wider mesh
    firmware_version: str = "1.0.0"   # the reporter's version
    # power (from monitor.ups.read_ups) — None fields => "not reported"
    battery_mv: Optional[int] = None
    battery_pct: Optional[int] = None
    on_battery: bool = False
    charging: bool = False
    on_solar: bool = False       # stamped at birth (the node's power source)
    on_mains: bool = False
    # the node's own view of its LoRa link (from RNS/RNode packet stats)
    lora_snr_db: Optional[int] = None
    lora_rssi_dbm: Optional[int] = None


def collect_pi_health(inp: PiHealthInputs) -> HealthBeacon:
    """Build a v2 :class:`HealthBeacon` for a Pi propagation node from injected
    readings. Pure — no OS, no hardware, no RNS.

    Flag mapping (the beacon was shaped for an ESP32; a Pi maps onto it):
      * wifi_up            -> has an IP network
      * lora_up            -> the attached RNode radio is up (critical)
      * tcp_backbone_up    -> RNS has a live transport interface
      * local_tcp_server_up-> N/A on a Pi propagation node (always False)
      * wdt_armed          -> True: runs under systemd with auto-restart + the
                              node's hardware watchdog (so it never spuriously
                              WARNs on the 'watchdog not armed' rule)
      * psram              -> N/A (False)
      * fault              -> disk critically full
    """
    fault = inp.disk_used_pct >= DISK_FAULT_PCT
    return HealthBeacon(
        format_version=FORMAT_VERSION_V2,
        uptime_s=max(0, inp.uptime_s),
        free_heap_kb=max(0, min(FREE_KB_CAP, inp.free_ram_kb)),
        wifi_rssi_dbm=0,                     # Pi wifi RSSI not carried here
        reset_reason=0,
        wifi_up=inp.net_up,
        lora_up=inp.radio_up,
        tcp_backbone_up=inp.rns_transport_up,
        local_tcp_server_up=False,
        wdt_armed=True,
        psram=False,
        fault=fault,
        airtime_lock=False,
        board_id=BOARD_PI_PROPAGATION,
        firmware_version=inp.firmware_version,
        battery_mv=inp.battery_mv,
        battery_pct=inp.battery_pct,
        on_battery=inp.on_battery,
        charging=inp.charging,
        on_solar=inp.on_solar,
        on_mains=inp.on_mains,
        lora_snr_db=inp.lora_snr_db,
        lora_rssi_dbm=inp.lora_rssi_dbm,
    )


def build_beacon_bytes(inp: PiHealthInputs) -> bytes:
    """The 20-byte v2 wire payload for a Pi node's ``rtnode.health`` announce."""
    return collect_pi_health(inp).to_bytes()


# ---- pure /proc + df parsers (unit-tested, no hardware) ---------------------

def parse_proc_uptime(text: str) -> int:
    """`/proc/uptime` -> whole seconds of uptime. First float is uptime_s."""
    try:
        return int(float(text.split()[0]))
    except (ValueError, IndexError):
        return 0


def parse_meminfo_free_kb(text: str) -> int:
    """`/proc/meminfo` -> available RAM in KB (MemAvailable, the honest 'free'
    figure). Falls back to MemFree, else 0."""
    avail = free = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "MemAvailable:":
            avail = int(parts[1])
        elif len(parts) >= 2 and parts[0] == "MemFree:":
            free = int(parts[1])
    if avail is not None:
        return avail
    return free if free is not None else 0


def disk_used_percent(used_bytes: int, total_bytes: int) -> int:
    """Percent of the filesystem in use (0..100). total<=0 -> 0."""
    if total_bytes <= 0:
        return 0
    return max(0, min(100, round(100 * used_bytes / total_bytes)))


# ---- thin OS transport (lazy; not imported in tests) -----------------------

def read_os_inputs(power_source: str = "battery",
                   firmware_version: str = "1.0.0") -> PiHealthInputs:
    """Gather the real readings on a live Pi propagation node.

    ``power_source`` is what BIRTH stamped ('solar' / 'mains' / 'battery').
    Best-effort: any reading that can't be taken is left at a safe default so
    the reporter still emits a beacon (a partial beacon beats grey silence).
    """
    import os
    import shutil

    try:
        with open("/proc/uptime") as f:
            uptime_s = parse_proc_uptime(f.read())
    except OSError:
        uptime_s = 0
    try:
        with open("/proc/meminfo") as f:
            free_ram_kb = parse_meminfo_free_kb(f.read())
    except OSError:
        free_ram_kb = 0
    try:
        du = shutil.disk_usage("/")
        disk_pct = disk_used_percent(du.used, du.total)
    except OSError:
        disk_pct = 0

    # Battery via the UPS driver (present=False when there's no HAT -> None).
    battery_mv = battery_pct = None
    on_battery = charging = False
    try:
        from monitor.ups import read_ups
        ups = read_ups()
        if ups.present:
            battery_mv = int(round(ups.voltage * 1000))
            battery_pct = ups.percent
            on_battery = True
            charging = ups.charging
    except Exception:
        pass

    src = (power_source or "").lower()
    return PiHealthInputs(
        uptime_s=uptime_s,
        free_ram_kb=free_ram_kb,
        disk_used_pct=disk_pct,
        net_up=_net_up(os),
        radio_up=True,           # refined by the runtime that owns the RNode
        rns_transport_up=True,   # refined by the runtime that owns RNS
        firmware_version=firmware_version,
        battery_mv=battery_mv,
        battery_pct=battery_pct,
        on_battery=on_battery,
        charging=charging,
        on_solar=(src == "solar"),
        on_mains=(src == "mains"),
    )


def _net_up(os_mod) -> bool:
    """True if any non-loopback interface has carrier (best-effort)."""
    base = "/sys/class/net"
    try:
        for iface in os_mod.listdir(base):
            if iface == "lo":
                continue
            try:
                with open(f"{base}/{iface}/operstate") as f:
                    if f.read().strip() == "up":
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


# ---- RNS announce runtime (lazy; runs on the live node) --------------------
#
# The node announces its beacon on the SAME destination the medic listens for
# and can command: RNS.Destination(identity, IN, SINGLE, "rtnode", "health").
# The medic maps that destination hash to the node's profile, so the identity
# must be STABLE across restarts (a birthed node keeps one identity file).
#
# Airtime ethos: 20 bytes on a slow heartbeat, plus an immediate reply when the
# medic sends a 0x01 command packet (the "commandable lighthouse", same contract
# the RTNode-2400 firmware honours).

#: Default heartbeat between unsolicited health announces. Airtime is the
#: scarcest shared resource, and health moves slowly, so the unsolicited beat is
#: deliberately sparse — one 20-byte beacon every 6 h (aligned with the mesh's
#: 6/12/18 h announce cadence). The operator gets an *instant* reading any time
#: by commanding a beacon from VITALS (a 0x01 packet -> immediate reply), so the
#: slow heartbeat costs almost nothing yet on-demand health is always available.
HEARTBEAT_S = 21600

#: Command byte the medic sends to request an immediate beacon (matches the
#: RTNode-2400 firmware's health_request_handler).
COMMAND_BEACON = 0x01

#: Where a birthed node keeps its stable health-reporter identity.
DEFAULT_IDENTITY_PATH = "~/.reticulum-node-medic/pi_health_identity"


def _load_or_create_identity(RNS, path: str):
    import os
    p = os.path.expanduser(path)
    if os.path.isfile(p):
        ident = RNS.Identity.from_file(p)
        if ident is not None:
            return ident
    ident = RNS.Identity()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    ident.to_file(p)
    return ident


def serve(power_source: str = "battery",
          firmware_version: str = "1.0.0",
          heartbeat_s: int = HEARTBEAT_S,
          identity_path: str = DEFAULT_IDENTITY_PATH) -> None:
    """Run the propagation-node health reporter (blocks). Announces a beacon on
    ``rtnode.health`` every ``heartbeat_s`` and immediately on a 0x01 command.

    Imports RNS lazily so this module stays importable where RNS is absent; the
    reporter is installed as a systemd service at BIRTH for propagation nodes.
    """
    import time
    import RNS

    RNS.Reticulum()
    identity = _load_or_create_identity(RNS, identity_path)
    dest = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE,
                           "rtnode", "health")

    def current_beacon() -> bytes:
        return build_beacon_bytes(read_os_inputs(power_source, firmware_version))

    def announce():
        try:
            dest.announce(app_data=current_beacon())
            RNS.log("Pi health: announced beacon", RNS.LOG_VERBOSE)
        except Exception as e:      # never let a bad read kill the heartbeat
            RNS.log(f"Pi health: announce failed: {e}", RNS.LOG_ERROR)

    def on_command(data, packet):
        # any packet to our health destination is a beacon request (the medic
        # sends a single 0x01 byte) -> reply immediately.
        announce()

    dest.set_packet_callback(on_command)

    # EVERY ANNOUNCE CARRIES A BEACON, not just the ones this loop makes.
    #
    # SkyFinger, 2026-08-11: the medic knew the node's health identity — so
    # announces were plainly arriving — and had never once stored a beacon. It
    # showed as LoRa-only in VITALS for days, which read as a broken node. The
    # node was fine.
    #
    # A destination announces in TWO ways. This loop calls announce() with the
    # beacon attached; but RNS ALSO re-announces a destination by itself
    # whenever someone requests a path to it — and that automatic announce
    # carries the destination's default app_data, which was nothing. The medic
    # probes paths constantly, so the announces it actually received were
    # overwhelmingly the empty ones: identity learned, health never.
    #
    # set_default_app_data takes a CALLABLE, evaluated at announce time, so an
    # automatic re-announce carries readings from that moment rather than a
    # stale snapshot taken at boot.
    try:
        dest.set_default_app_data(current_beacon)
    except Exception as e:           # older RNS without the callable form
        RNS.log(f"Pi health: default app_data unavailable: {e}", RNS.LOG_ERROR)

    announce()                       # beacon once on startup
    next_at = time.time() + heartbeat_s
    while True:
        time.sleep(1)
        if time.time() >= next_at:
            announce()
            next_at = time.time() + heartbeat_s


if __name__ == "__main__":          # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="Pi propagation-node health reporter")
    ap.add_argument("--power-source", default="battery",
                    choices=["battery", "solar", "mains"])
    ap.add_argument("--firmware-version", default="1.0.0")
    ap.add_argument("--heartbeat", type=int, default=HEARTBEAT_S)
    ap.add_argument("--identity", default=DEFAULT_IDENTITY_PATH)
    a = ap.parse_args()
    serve(power_source=a.power_source, firmware_version=a.firmware_version,
          heartbeat_s=a.heartbeat, identity_path=a.identity)
