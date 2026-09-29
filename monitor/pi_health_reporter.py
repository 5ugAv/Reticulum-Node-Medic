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

from dataclasses import dataclass, field
from typing import Optional

from monitor.health_beacon import (
    HealthBeacon,
    BOARD_PI_PROPAGATION,
    FORMAT_VERSION_V2, FORMAT_VERSION_V4,
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
    #: Bluetooth truth for the beacon's KNOWN/UP bit pair: True = adapter
    #: present and not rfkill-blocked, False = absent or blocked, None = could
    #: not be read (stays honestly unreported on the wire).
    bt_up: Optional[bool] = None
    on_mains: bool = False
    # the node's own view of its LoRa link (from RNS/RNode packet stats)
    lora_snr_db: Optional[int] = None
    lora_rssi_dbm: Optional[int] = None
    #: Who THIS node hears: ``[(dest_hash_bytes_or_hex, snr_db|None, age_s)]``.
    #: Empty means "heard nobody worth reporting" AND "not gathered" alike —
    #: see collect_pi_health for why the beacon stays v2 then.
    neighbours: list = field(default_factory=list)


def _short(dest_hash):
    """The 16-bit pointer a neighbour entry carries — see health_beacon.short_hash."""
    from monitor.health_beacon import short_hash
    return short_hash(dest_hash)


def gather_neighbours(now=None, run=None, timeout_s: float = 30.0) -> list:
    """Who this node hears, from ITS OWN path table: ``rnpath -t --json`` rows
    at one hop, with how long ago each was last updated. One hop is the
    honest bar — a node this one reaches only through another is that other
    node's neighbour, not ours. No SNR: the path table does not carry one, and
    a guessed figure is worse than none. Empty on any failure (no rnpath, no
    RNS, a parse error): a missing report costs a line on a map, a wrong one
    draws a link that is not there.
    """
    import json
    import subprocess
    import time as _time
    now = _time.time() if now is None else now
    try:
        if run is None:
            raw = subprocess.run(["bash", "-lc", "rnpath -t --json 2>/dev/null"],
                                 capture_output=True, text=True,
                                 timeout=timeout_s).stdout
        else:
            raw = run("rnpath -t --json")
        rows = json.loads(raw or "[]")
    except Exception:                                              # noqa: BLE001
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            if int(r.get("hops", 99)) != 1:
                continue
            h = str(r.get("hash", "")).strip()
            ts = float(r.get("timestamp", 0) or 0)
            if len(h) < 4 or ts <= 0:
                continue
            out.append((h, None, max(0.0, now - ts)))
        except (TypeError, ValueError):
            continue
    return out


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
    # v4 ONLY WHEN THERE IS SOMETHING TO SAY. A v4 beacon with an empty
    # neighbour list is 10 bytes longer than v2 (the position slot plus the
    # count byte) on every beacon, forever, to say "nobody" — and LoRa airtime
    # is the scarcest shared thing this tool spends. So a node that hears
    # nobody keeps sending v2, and the medic reads that as "not reported"
    # rather than "isolated", which is the safe reading either way.
    version = FORMAT_VERSION_V4 if inp.neighbours else FORMAT_VERSION_V2
    return HealthBeacon(
        format_version=version,
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
        bt_up=inp.bt_up,
        lora_snr_db=inp.lora_snr_db,
        lora_rssi_dbm=inp.lora_rssi_dbm,
        neighbours=[{"short_hash": _short(h), "snr_db": snr,
                     "age_s": age}
                    for (h, snr, age) in inp.neighbours
                    if _short(h) is not None],
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
    neighbours = gather_neighbours()
    return PiHealthInputs(
        neighbours=neighbours,
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
        bt_up=_read_bt_up(),
    )


def bt_state(has_adapter: bool,
             rfkill_soft: Optional[str],
             rfkill_hard: Optional[str]) -> Optional[bool]:
    """Bluetooth truth from injected sysfs facts (pure, unit-tested).

    True  = an adapter is registered and rfkill shows it unblocked;
    False = no adapter, or rfkill soft/hard-blocked (the birth's "Bluetooth
            off" is exactly `rfkill block bluetooth`);
    None  = there IS an adapter but rfkill state could not be read — unknown,
            which the beacon encodes as "not reported" (SolarLove rule:
            never promote a datasheet to a state).
    """
    if not has_adapter:
        return False
    if rfkill_soft is None and rfkill_hard is None:
        return None
    return (rfkill_soft or "0").strip() == "0" and \
           (rfkill_hard or "0").strip() == "0"


def _read_bt_up() -> Optional[bool]:
    """Best-effort sysfs read feeding :func:`bt_state` on a live node."""
    import os
    try:
        has_adapter = bool(os.listdir("/sys/class/bluetooth"))
    except OSError:
        has_adapter = False
    soft = hard = None
    try:
        base = "/sys/class/rfkill"
        for rk in os.listdir(base):
            try:
                with open(f"{base}/{rk}/type") as f:
                    if f.read().strip() != "bluetooth":
                        continue
                with open(f"{base}/{rk}/soft") as f:
                    soft = f.read()
                with open(f"{base}/{rk}/hard") as f:
                    hard = f.read()
                break
            except OSError:
                continue
    except OSError:
        pass
    return bt_state(has_adapter, soft, hard)


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


def send_time_ack(rns, identity, dest, nonce, before, status, trust_path=None,
                  log=None, warm_wait_s: float = 10.0) -> bool:
    """One signed TIME_ACK to the trusted medic's reply destination. True
    when .send() returned; False (said why) when the medic's identity is
    not recalled, no path could be warmed, or the send raised. Shared by
    the 0x05 handler and the once-after-the-next-announce retry (review
    item 12, 2026-09-23)."""
    from monitor import node_time
    from monitor.health_poll import warm_path
    from monitor.health_reply import REPLY_APP, REPLY_ASPECTS, build_time_ack
    _log = log or (lambda m, lvl=None: None)
    trust_path = trust_path or node_time.TRUST_PATH
    try:
        trust = node_time.load_trust(trust_path)
        medic = node_time.recall_trusted_medic(rns, trust) if trust else None
        if trust is None or medic is None:
            _log("TIME_ACK not sent: no %s for the medic's reply destination" % (
                "trust file" if trust is None else "identity"))
            return False
        reply_dest = bytes.fromhex(trust["reply_dest"])
        has_path = warm_path(reply_dest, rns.Transport.has_path,
                             rns.Transport.request_path, wait_s=warm_wait_s)
        if not has_path:
            _log("TIME_ACK not sent: no path to the medic's reply destination "
                 "(path requested)")
            return False
        out = rns.Destination(medic, rns.Destination.OUT,
                              rns.Destination.SINGLE, REPLY_APP, *REPLY_ASPECTS)
        rns.Packet(out, build_time_ack(bytes(dest.hash), nonce, before, status,
                                       identity.sign)).send()
        _log("TIME_ACK sent to medic %s: status=%d before=%d" % (
            trust["identity_hash"][:8], int(status), before))
        return True
    except Exception as e:                                         # noqa: BLE001
        _log("TIME_ACK failed: %s" % e)
        return False


def make_command_handler(rns, identity, dest, announce, current_beacon,
                         log=None, spawn=None, warm_wait_s: float = 10.0,
                         trust_path=None, run=None, now=None, time_state=None,
                         ntp=None, state_path=None, monotonic=None):
    """The packet callback for the health destination — the same contract
    the firmware implements (docs/HEALTH_REPLY_UNICAST.md, 2026-09-21):

      0x01                -> announce the beacon (today's reply)
      0x04 | dest | nonce -> reply by UNICAST to *dest* when this node can
                             recall the medic's identity AND has a path
                             back; otherwise announce now (the path warmer
                             has already asked for a road, so the next poll
                             is unicast). Off the callback thread: warming
                             a path takes seconds.
      0x05 | time | nonce | sig
                          -> the TIME (2026-09-23, "Time over the mesh"):
                             accepted ONLY from the trusted medic on file,
                             refused when it is not newer than the last
                             applied (the floor) or when this clock is
                             NTP-synchronised, applied through the root
                             helper when more than 30 s off, and answered
                             with a signed TIME_ACK carrying a status byte
                             — asked for or not. An ack that cannot be sent
                             is kept for one retry after the next announce.
      anything else       -> ignored, like the firmware.

    *rns* is the RNS module (injected so tests use a fake); *spawn* runs the
    reply work (a daemon thread by default; tests run it inline); *run*,
    *now*, *ntp*, *state_path* and *trust_path* are the clock's outside
    world (tests inject); *time_state* is shared with the asker (the
    issued nonces, the last set, the ack to retry)."""
    import threading
    import time as _time
    from monitor import node_time
    from monitor.health_poll import warm_path
    from monitor.health_reply import (OPCODE_HEALTH_TO, OPCODE_TIME, REPLY_APP,
                                      REPLY_ASPECTS, TIME_STATUS_SET, make_reply,
                                      node_reply_mode, parse_request)
    _log = log or (lambda m, lvl=None: None)
    if spawn is None:
        def spawn(fn, *a):
            threading.Thread(target=fn, args=a, daemon=True).start()
    trust_path = trust_path or node_time.TRUST_PATH
    run = run or node_time.run_argv
    ntp = ntp or node_time.ntp_synchronized
    state_path = state_path or node_time.TIME_STATE_PATH
    _mono = monotonic or _time.monotonic
    state = time_state if time_state is not None else node_time.TimeState()

    def handle_time(data):
        # Fresh NTP word for THIS packet, on this worker thread (never the
        # callback thread): a node whose internet came back since the last
        # ask must not have its clock moved by a TIME (review item 9).
        synced = ntp()
        state.ntp_synced = synced
        got = node_time.handle_time(bytes(dest.hash), data, rns, trust_path=trust_path,
                                    now=now, run=run, log=_log, ntp_synced=synced,
                                    state_path=state_path, issued=state.issued)
        if got is None:
            return                       # said why already; nothing to ack
        nonce, before, status = got
        if status == TIME_STATUS_SET:
            state.last_set_at = _mono()  # re-ask after RE_ASK_S, no latch
        if not send_time_ack(rns, identity, dest, nonce, before, status,
                             trust_path=trust_path, log=_log, warm_wait_s=warm_wait_s):
            state.pending_ack = (nonce, before, status)
            _log("TIME_ACK kept for one retry after the next announce")

    def reply_unicast(reply_dest, nonce):
        try:
            ident = rns.Identity.recall(reply_dest)
            has_path = warm_path(reply_dest, rns.Transport.has_path,
                                 rns.Transport.request_path, wait_s=warm_wait_s)
            mode = node_reply_mode(ident is not None, bool(has_path))
            if mode != "unicast":
                _log("0x04: no %s to the medic - announcing instead" % (
                    "identity" if ident is None else "path"))
                announce()
                return
            out = rns.Destination(ident, rns.Destination.OUT,
                                  rns.Destination.SINGLE, REPLY_APP, *REPLY_ASPECTS)
            payload = make_reply(bytes(dest.hash), nonce, current_beacon(),
                                 identity.sign)
            rns.Packet(out, payload).send()
            _log("0x04: unicast health reply sent")
        except Exception as e:                                     # noqa: BLE001
            _log("0x04: reply failed: %s - announcing instead" % e)
            try:
                announce()
            except Exception:                                      # noqa: BLE001
                pass

    def on_command(data, packet):
        op, reply_dest, nonce = parse_request(bytes(data or b""))
        if op == OPCODE_HEALTH_TO:
            spawn(reply_unicast, reply_dest, nonce)
        elif op == COMMAND_BEACON:
            announce()
        elif op == OPCODE_TIME:
            spawn(handle_time, bytes(data or b""))
        # unknown or malformed: ignored, so the registry of opcodes can grow
    return on_command


def retry_pending_ack(rns, identity, dest, time_state, trust_path=None, log=None,
                      warm_wait_s: float = 10.0) -> bool:
    """After an announce: send the one ack that could not go out earlier,
    once. The announce is what makes the medic learn a road to this node
    — and request one back — so the retry has its best chance right
    after it. Whether it goes or not, the slot is cleared: ONE retry."""
    pending = getattr(time_state, "pending_ack", None)
    if pending is None:
        return False
    time_state.pending_ack = None
    nonce, before, status = pending
    return send_time_ack(rns, identity, dest, nonce, before, status,
                         trust_path=trust_path, log=log, warm_wait_s=warm_wait_s)


def make_time_asker(rns, dest, trust_path=None, log=None, warm_wait_s: float = 10.0,
                    time_state=None):
    """The TIME_REQ sender (2026-09-23): returns ``ask()`` -> True when a
    request went out. Sends ONLY when the trust file exists, the trusted
    medic's identity is recalled (an announce from its reply destination
    was heard) and a path exists or can be warmed — the same predicate
    the unicast reply uses. Every refusal is said in *log* and counted in
    *time_state* (identical refusals in a row back the schedule off); the
    nonce of a sent request is remembered so the answer reads "asked"."""
    from monitor import node_time
    from monitor.health_poll import warm_path
    from monitor.health_reply import NONCE_LEN, REPLY_APP, REPLY_ASPECTS, build_time_req
    _log = log or (lambda m, lvl=None: None)
    trust_path = trust_path or node_time.TRUST_PATH
    state = time_state if time_state is not None else node_time.TimeState()

    def _refuse(reason: str, msg: str) -> bool:
        state.note_refusal(reason)
        _log(msg)
        return False

    def ask() -> bool:
        try:
            trust = node_time.load_trust(trust_path)
            if trust is None:
                return _refuse("no_trust", "TIME_REQ not sent: no trusted medic on file (%s)"
                               % trust_path)
            medic = node_time.recall_trusted_medic(rns, trust)
            if medic is None:
                return _refuse("not_recalled", "TIME_REQ not sent: trusted medic %s not "
                               "recalled yet (no announce heard from %s)"
                               % (trust["identity_hash"][:8], trust["reply_dest"][:8]))
            reply_dest = bytes.fromhex(trust["reply_dest"])
            has_path = warm_path(reply_dest, rns.Transport.has_path,
                                 rns.Transport.request_path, wait_s=warm_wait_s)
            if not has_path:
                return _refuse("no_path", "TIME_REQ not sent: no path to the medic's reply "
                               "destination %s (path requested)" % trust["reply_dest"][:8])
            out = rns.Destination(medic, rns.Destination.OUT,
                                  rns.Destination.SINGLE, REPLY_APP, *REPLY_ASPECTS)
            import os as _os
            nonce = _os.urandom(NONCE_LEN)
            state.issued.issue(nonce)
            rns.Packet(out, build_time_req(bytes(dest.hash), nonce)).send()
            state.note_success()
            _log("TIME_REQ sent to medic %s (%s)" % (trust["identity_hash"][:8],
                                                     trust.get("name") or "unnamed"))
            return True
        except Exception as e:                                     # noqa: BLE001
            return _refuse("error", "TIME_REQ failed: %s" % e)
    return ask


def run_time_asker(ask, time_state, log=None, ntp=None, monotonic=None,
                   sleep=None, stop=None) -> None:
    """The asker's own loop, for its own daemon thread (never the
    heartbeat's): every second, if node_time.should_ask says so, re-read
    timedatectl (a shell call — a node that lost its internet at sunset
    must start asking again), then ask. All stamps monotonic. *stop* is
    a callable that ends the loop (tests)."""
    import time as _time
    from monitor import node_time
    _log = log or (lambda m, lvl=None: None)
    ntp = ntp or node_time.ntp_synchronized
    _mono = monotonic or _time.monotonic
    _sleep = sleep or _time.sleep
    started_at = _mono()
    last_ntp_read_at = None
    while not (stop and stop()):
        _sleep(1)
        now = _mono()
        if now - started_at < node_time.ASK_GRACE_S:
            continue
        # timedatectl is re-read on ITS OWN cadence (every ASK_EVERY_S),
        # never gated on the last answer: a node whose internet went with
        # the sun said "yes" at noon and must be asked again at dusk.
        if last_ntp_read_at is not None and now - last_ntp_read_at < node_time.ASK_EVERY_S:
            continue
        synced = ntp()
        time_state.ntp_synced = synced
        last_ntp_read_at = now
        if synced is True:
            _log("clock is NTP-synchronised - not asking the medic for time")
            continue
        if not node_time.should_ask(now, started_at, time_state.last_ask_at,
                                    time_state.last_set_at, synced,
                                    time_state.consecutive_refusals):
            continue
        time_state.last_ask_at = now
        _log("clock not NTP-synchronised (timedatectl says %s)%s - asking the medic"
             % ("no" if synced is False else "unreadable",
                " again, %d h after the last set" % (node_time.RE_ASK_S // 3600)
                if time_state.last_set_at is not None else ""))
        ask()


def serve(power_source: str = "battery",
          firmware_version: str = "1.0.0",
          heartbeat_s: int = HEARTBEAT_S,
          identity_path: str = DEFAULT_IDENTITY_PATH) -> None:
    """Run the propagation-node health reporter (blocks). Announces a beacon on
    ``rtnode.health`` every ``heartbeat_s`` and immediately on a 0x01 command.

    Imports RNS lazily so this module stays importable where RNS is absent; the
    reporter is installed as a systemd service at BIRTH for propagation nodes.
    """
    import threading
    import time
    import RNS
    from monitor import node_time

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

    # NOTICE, not VERBOSE: a 0x04 is rare and is the evidence a bench
    # proof reads back from the journal (2026-09-22) — and journald drops
    # VERBOSE, so every time-over-the-mesh line goes at NOTICE too.
    def _nlog(m, lvl=None):
        RNS.log("Pi health: " + m, lvl or RNS.LOG_NOTICE)

    time_state = node_time.TimeState()
    dest.set_packet_callback(make_command_handler(
        RNS, identity, dest, announce, current_beacon, log=_nlog,
        trust_path=node_time.TRUST_PATH, time_state=time_state))
    ask_time = make_time_asker(RNS, dest, trust_path=node_time.TRUST_PATH, log=_nlog,
                               time_state=time_state)

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

    # TIME OVER THE MESH (2026-09-23): the asker on its OWN daemon thread —
    # it shells out to timedatectl and warms paths (seconds), neither of
    # which belongs on the heartbeat. See run_time_asker.
    threading.Thread(target=run_time_asker, args=(ask_time, time_state),
                     kwargs={"log": _nlog}, daemon=True).start()

    announce()                       # beacon once on startup
    # MONOTONIC deadlines, never time.time(): the whole point of this node
    # is that its wall clock gets SET — a backwards set once silenced the
    # beacon for the size of the jump (review, 2026-09-23).
    next_at = time.monotonic() + heartbeat_s
    while True:
        time.sleep(1)
        if time.monotonic() >= next_at:
            announce()
            next_at = time.monotonic() + heartbeat_s
            # an ack that could not go out earlier gets its one retry now
            # (the announce is what gives both sides a road)
            if time_state.pending_ack is not None:
                threading.Thread(target=retry_pending_ack,
                                 args=(RNS, identity, dest, time_state),
                                 kwargs={"trust_path": node_time.TRUST_PATH, "log": _nlog},
                                 daemon=True).start()


if __name__ == "__main__":          # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="Pi propagation-node health reporter")
    ap.add_argument("--power-source", default="battery",
                    # "unknown" is what BIRTH stamps when nobody has been
                    # asked (honest default, 2026-08-13) — rejecting it
                    # crash-looped rnm-health on the first node built after
                    # that change (EVERYWHERE, 2026-08-14, exit 2). The
                    # reporter accepts what birth is allowed to say.
                    choices=["battery", "solar", "mains", "unknown"])
    ap.add_argument("--firmware-version", default="1.0.0")
    ap.add_argument("--heartbeat", type=int, default=HEARTBEAT_S)
    ap.add_argument("--identity", default=DEFAULT_IDENTITY_PATH)
    a = ap.parse_args()
    serve(power_source=a.power_source, firmware_version=a.firmware_version,
          heartbeat_s=a.heartbeat, identity_path=a.identity)
