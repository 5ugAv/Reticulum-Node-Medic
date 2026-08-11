"""Build path for standalone RTNode-2400 (Type B) nodes.

The Pi build path (workflows/build.py) SSHes/serials into a node and installs a
software stack. A Type B build is different: the tool (the Pi 5 medic) flashes
an attached Heltec V4 over USB with PlatformIO, then confirms the flash by
hearing the board's first health beacon. WiFi/LoRa onboarding happens through
the firmware's own captive portal (SSID ``RTNode-Setup`` -> ``http://10.0.0.1``),
which is a human step until that portal's HTTP contract is available.

This mirrors the carried human-friendly flasher
(assets/scripts/flash_rtnode2400.sh) but runs programmatically and is testable
against an EmulatedConnection. The ``connection`` here represents the tool's
local shell plus the attached board.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from node_profile import NodeHardware, NodeProfile
from transport.connection import Connection
from diagnostics.rtnode_2400 import CAPTURE_COMMAND
from monitor.health_beacon import HealthBeacon, decode
from monitor.geo import GpsFix, read_gps
from workflows.build import StepResult
from workflows.rtnode_portal import build_form

#: Firmware provenance — the tool flashes this exact repo/branch. Verified
#: against the firmware source: platformio.ini defines env
#: ``heltec_V4_boundary-local``, and the carried human flasher
#: (assets/scripts/flash_rtnode2400.sh) clones the same repo/branch. Keep all
#: three in lockstep (see test_rtnode_build).
RTNODE_REPO_URL = "https://github.com/5ugAv/RTNode-2400.git"
RTNODE_BRANCH = "feature/neopixel-status-led"
#: PlatformIO build environment for the Heltec V4 RTNode-2400 target. Kept as a
#: module constant because the carried human flasher + provenance tests pin it.
RTNODE_BUILD_ENV = "heltec_V4_boundary-local"


@dataclass(frozen=True)
class RTNodeTarget:
    """A board the RTNode-2400 build path can flash. Both are ESP32-S3 native-USB
    (indistinguishable by USB id), so the operator picks the target and it selects
    the PlatformIO env + how the flash is verified."""
    key: str
    display: str
    build_env: str
    hardware: "NodeHardware"
    verify: str = "beacon"      # "beacon" (health beacon) | "sd_status" (/status)


#: Operator-selected RTNode-2400 targets. Verified against platformio.ini on
#: branch feature/neopixel-status-led (commit 88d9aaf): both envs exist; the
#: Supreme carries the SD-overflow transport tier (-DFILESYSTEM_SD_OVERFLOW=1),
#: verified via its GET /status sd_overflow object rather than a health beacon.
RTNODE_TARGETS = {
    "heltec_v4": RTNodeTarget(
        "heltec_v4", "Heltec V4", RTNODE_BUILD_ENV,
        NodeHardware.HELTEC_V4, verify="beacon"),
    # V3 and V4 are BOTH ESP32-S3 native-USB — the medic can't tell them apart, so
    # the operator confirms. V3 has its own PlatformIO env (heltec_V3_boundary:
    # heltec_wifi_lora_32_V3 board, 8MB, no NeoPixel — V3 has no onboard RGB).
    "heltec_v3": RTNodeTarget(
        "heltec_v3", "Heltec V3", "heltec_V3_boundary",
        NodeHardware.HELTEC_V3, verify="beacon"),
    "tbeam_supreme": RTNodeTarget(
        "tbeam_supreme", "T-Beam Supreme (SD transport node)",
        "tbeam_supreme_boundary-local", NodeHardware.TBEAM_SUPREME,
        verify="sd_status"),
}
DEFAULT_TARGET = "heltec_v4"


def check_sd_overflow(status_json: str) -> Tuple[bool, str]:
    """Assert an SD-overflow node's card mounted, from its GET /status JSON
    (``sd_overflow`` object). A fresh node's /destination_table is empty until it
    learns paths, so `mounted` is the hard requirement; the path table is only a
    note. Returns ``(ok, human_detail)``."""
    try:
        data = json.loads(status_json) if status_json.strip() else {}
    except ValueError:
        return False, "Node /status returned invalid JSON."
    sd = data.get("sd_overflow")
    if not isinstance(sd, dict):
        return False, ("Node /status has no sd_overflow object — the SD tier "
                       "isn't built into this firmware.")
    if not sd.get("mounted"):
        return False, ("SD card is not mounted (check the card seating / the "
                       "AXP2101 BLDO1 rail that powers it).")
    files = sd.get("files") or []
    has_dt = any("destination_table" in str(f) for f in files)
    return True, (
        f"SD tier up: {sd.get('card_mb', '?')} MB card, "
        f"{sd.get('used_kb', '?')} KB used"
        + ("; path table present." if has_dt
           else "; /destination_table not written yet (fills as paths are learned)."))
#: Firmware project location on the tool (carried/cloned asset). The Pi medic is
#: headless, so this is under the medic home, not ~/Desktop like the Mac flasher.
RTNODE_PROJECT_DIR = "~/rnm-assets/RTNode2400"
#: Onboarding access point the firmware raises after a fresh flash.
ONBOARDING_SSID = "RTNode-Setup"
ONBOARDING_URL = "http://10.0.0.1"

# The medic is a Pi (Linux, /dev/ttyACM* — verified: a real Heltec V4 enumerates
# as ttyACM0), but the tool must also run from a Mac (/dev/cu.*). List Linux
# globs first so Pi detection wins on the platform the tool actually ships on.
_PORT_GLOBS = ("/dev/ttyACM*", "/dev/ttyUSB*",
               "/dev/cu.usbmodem*", "/dev/cu.usbserial*",
               "/dev/cu.wchusbserial*", "/dev/cu.SLAB_USBtoUART*")
_BEACON_RE = re.compile(r"\[HealthBeacon\][^\n]*dst=([0-9a-fA-F]+)[^\n]*data=([0-9a-fA-F]+)")
_INIT_RE = re.compile(r"\[HealthBeacon\] init dst=([0-9a-fA-F]+)")

#: Injectable sleep so the single-pass retry logic is unit-testable.
_sleep = time.sleep

_RTNODE_STEPS: List[Tuple[str, Callable]] = []


# ---- single-pass helpers (2026-07-30: believe OUTCOMES, not plumbing) --------

def board_mac_from_port(wf) -> str:
    """The ESP32's MAC from its /dev/serial/by-id symlink (…_AA:BB:…:FF-if00).
    Empty string when unknown."""
    out = wf.connection.run("ls -l /dev/serial/by-id/ 2>/dev/null")[1]
    port = (wf.profile.connection_port or "").split("/")[-1]
    for line in out.splitlines():
        if port and line.strip().endswith(port):
            m = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", line)
            if m:
                return m.group(1)
    return ""


def default_lan_host(mac: str) -> str:
    """The firmware's default mDNS hostname: ``rtnode`` + last two MAC octets
    (verified live: MAC 02:00:00:07:00:07 -> rtnode0007.local)."""
    if not mac:
        return ""
    return "rtnode" + mac.replace(":", "").lower()[-4:] + ".local"


def board_configured_on_lan(wf) -> Tuple[bool, str]:
    """OUTCOME check: a CONFIGURED board joins the medic's LAN and serves
    ``GET /status``. This is the ground truth that survives plumbing hiccups —
    a run whose rejoin failed AFTER the portal POST landed still configured the
    board (the 2026-07-30 'stuck at provisioning' spiral: every retry hunted a
    portal that no longer existed)."""
    host = default_lan_host(board_mac_from_port(wf))
    if not host:
        return (False, "board MAC unknown — cannot derive its LAN name")
    out = wf.connection.run(f"curl -s -m 6 http://{host}/status")[1]
    if '"fw_version"' in (out or ""):
        m = re.search(r'"node_name"\s*:\s*"([^"]*)"', out)
        name = m.group(1) if m else "?"
        return (True, f"{host} is up and serving /status (node_name '{name}')")
    return (False, f"{host} not answering /status")


def serial_capture_cmd(port: str, seconds: int = 55) -> str:
    """Reset the board (RTS pulse) and capture its boot log from serial — the
    REAL capture behind verify_beacon. (The old CAPTURE_COMMAND invoked
    ``rnm-serial-capture``, which does not exist on the medic: verify_beacon
    failed on EVERY birth, poisoning otherwise-successful runs.) ~`seconds`
     1s-timeout reads ≈ that many seconds — long enough for boot + the first
    health announce (~30 s after reset)."""
    return ("python3 -c \"import serial,time; "
            f"s=serial.Serial('{port}',115200,timeout=1); "
            "s.dtr=False; s.rts=True; time.sleep(0.1); s.rts=False; "
            f"d=b''.join(s.read(4096) for _ in range({seconds})); "
            "s.close(); print(d.decode('utf-8','replace'))\"")


def rtnode_build_step(func: Callable) -> Callable:
    _RTNODE_STEPS.append((func.__name__, func))
    return func


@rtnode_build_step
def detect_board(wf: "RTNodeBuildWorkflow") -> StepResult:
    # CRITICAL: prefer the work-board port pinned by the caller. On the medic
    # ttyACM0 is Jonesey (its OWN radio) and ttyACM1 is the work board, so a naive
    # "first /dev/ttyACM*" would flash the medic's own radio. board_port comes from
    # local_board_ports(), which excludes onboard boards.
    if wf.board_port:
        present = wf.connection.run(f"ls {wf.board_port} 2>/dev/null")[1].split()
        if not present:
            return StepResult("detect_board", False,
                              f"The board's port {wf.board_port} disappeared — "
                              f"replug the {wf.target.display} with a known-good "
                              "USB data cable, then build again.")
        wf.profile.hardware = wf.target.hardware
        wf.profile.connection_port = wf.board_port
        wf.profile.radio.serial_port = wf.board_port
        return StepResult("detect_board", True,
                          f"Using {wf.target.display} on {wf.board_port} "
                          "(the medic's own radio is excluded).")
    out = wf.connection.run(f"ls {' '.join(_PORT_GLOBS)} 2>/dev/null")[1]
    ports = out.split()
    if not ports:
        return StepResult("detect_board", False,
                          f"No board found — plug in the {wf.target.display} "
                          f"(try another USB-C cable; some are charge-only).")
    port = ports[0]
    # More than one USB-serial device present: don't silently guess. (T-Beam
    # Supreme and Heltec V4 are both ESP32-S3 native-USB, so the operator's
    # chosen target — not USB id — decides which firmware gets flashed.)
    extra = ("" if len(ports) == 1 else
             f" WARNING: {len(ports)} USB-serial devices seen "
             f"({', '.join(ports)}); using {port}. Unplug the others to be sure "
             f"you flash the right board.")
    wf.profile.hardware = wf.target.hardware
    wf.profile.connection_port = port
    wf.profile.radio.serial_port = port
    return StepResult("detect_board", True,
                      f"Found {wf.target.display} on {port}.{extra}")


@rtnode_build_step
def flash_firmware(wf: "RTNodeBuildWorkflow") -> StepResult:
    port = wf.profile.connection_port
    # HARD GATE (belt-and-suspenders behind local_board_ports): never upload to
    # the medic's OWN radio, whatever selected this port. Runs on the medic; a
    # non-medic host (no roster, plain serial) has no onboard board to protect.
    try:
        from ui.onboard_roster import (assert_flashable, guard_is_active,
                                        ProtectedBoardError)
        if guard_is_active():             # enforce on the medic; skip on dev/CI
            try:
                assert_flashable(port)
            except ProtectedBoardError as e:
                return StepResult("flash_firmware", False, str(e))
    except ImportError:
        pass                              # roster module unavailable off-medic

    # THROTTLE the compile: only 2 of the Pi's 4 cores, at low priority (nice 15),
    # so it can never overload the medic — the touchscreen stays smooth AND the live
    # radio (rnsd), GPS splitter and mesh keep running during a flash. A bit slower
    # than racing all cores, but the system stays responsive (progress ring fills
    # smoothly instead of freezing). Timeout raised to 900s to allow the gentler pace.
    cmd = (f"cd {RTNODE_PROJECT_DIR} && "
           f"nice -n 15 pio run -j 2 -e {wf.target.build_env} "
           f"-t upload --upload-port {port}")
    # SINGLE-PASS: the upload occasionally hits a transient (USB re-enumeration
    # mid-reset, port briefly busy) — verified 2026-07-30: a failed upload
    # succeeded by hand 3 minutes later, same command/port. Retry in-step so a
    # blip never surfaces as a failed birth.
    last = ""
    for attempt in (1, 2, 3):
        code, out, err = wf.connection.run(cmd, timeout=900)
        if code == 0:
            note = "" if attempt == 1 else f" (attempt {attempt})"
            return StepResult("flash_firmware",
                              True, f"Flashed RTNode-2400 firmware "
                                    f"({wf.target.display}).{note}")
        last = (err or out or "").strip()[-400:]
        _sleep(6)                      # give USB a breath to re-enumerate
    return StepResult("flash_firmware", False, f"Flash failed: {last}")


@rtnode_build_step
def wifi_onboarding(wf: "RTNodeBuildWorkflow") -> StepResult:
    # The firmware raises its own captive portal (POST /save) for WiFi/LoRa
    # setup. The tool builds the real form with recommended LoRa params
    # pre-filled; node name + WiFi credentials are operator-supplied. Actual
    # submission (join AP -> POST) happens when the operator provides creds.
    #
    # This MUST come before verify_beacon: a fresh, un-onboarded board blocks in
    # the captive portal in setup() and never reaches health_beacon_init(), so
    # it stays silent (both LoRa and USB) until config is saved and it reboots.
    #
    # The Pi is physically at the node now, so its GPS fix IS the node's
    # location. Capture it and pre-fill the advertisement (privacy-fuzzed on the
    # public map, exact on the birth certificate). No fix -> advertisement off.
    fix = (read_gps(wf.gps_reader) if wf.gps_reader is not None else read_gps())
    wf.gps_fix = fix
    lat = fix.lat if fix else None
    lon = fix.lon if fix else None

    # AUTO-provision: the medic joins the board's RTNode-Setup AP and POSTs /save
    # (node name + the medic's OWN WiFi so the node joins the same LAN + our LoRa
    # params + fuzzed location), then rejoins its own WiFi. Falls back to printing
    # manual portal instructions when auto-provision is off / creds unavailable.
    if wf.auto_provision and wf._provision and wf._wifi_credentials:
        ssid, psk = wf._wifi_credentials()
        wf.onboarding = build_form(wf.profile, node_name=wf.node_name,
                                   wifi_ssid=ssid, wifi_password=psk, lat=lat, lon=lon)
        if not ssid:
            return StepResult("wifi_onboarding", False,
                              "Can't auto-provision: the medic isn't on WiFi to share "
                              "with the node. Join WiFi, or configure the node manually "
                              f"at {ONBOARDING_URL}.")
        # An EMPTY psk means the secret read failed (sudo/nmcli), not an open
        # network we can join — provisioning with it silently births a node
        # that can never reach WiFi (2026-08-01 bug hunt). Say so instead.
        if not psk:
            return StepResult(
                "wifi_onboarding", False,
                f"Can't auto-provision: the medic couldn't read the WiFi "
                f"password for '{ssid}', so the node would be configured with "
                f"a blank one and never join. Configure it manually at "
                f"{ONBOARDING_URL}, or fix the medic's WiFi secret access.")
        # SINGLE-PASS discipline (2026-07-30): believe OUTCOMES, not plumbing.
        # 0) Already configured? Then there IS no portal — that's a birth that
        #    already landed (a previous pass whose tail failed), not a failure.
        cfg, who = board_configured_on_lan(wf)
        if cfg:
            return StepResult("wifi_onboarding", True,
                              f"Board is already configured — {who}. Portal "
                              "onboarding not needed.")
        # 1) Provision, with a full-cycle retry; after any claimed failure,
        #    RE-CHECK the outcome — the POST may have landed even though the
        #    medic's rejoin/tail hiccupped.
        ok, msg = False, "not attempted"
        for attempt in (1, 2):
            ok, msg = wf._provision(wf.profile, wf.node_name, ssid, psk,
                                    lat=lat, lon=lon, join_ap=wf._join_ap,
                                    post=wf._post, rejoin=wf._rejoin)
            if ok:
                break
            _sleep(20)                # board may be saving + rebooting + joining
            cfg, who = board_configured_on_lan(wf)
            if cfg:
                ok = True
                msg = (f"Config landed — {who} (despite: {msg}).")
                break
        return StepResult("wifi_onboarding", ok, msg)

    wf.onboarding = build_form(wf.profile, node_name=wf.node_name, lat=lat, lon=lon)
    f = wf.onboarding
    # SAY WHICH OF THE THREE IT ACTUALLY IS. This line used to read the fuzzed
    # coordinates straight out of the form on the strength of a GPS fix
    # existing — which is now only half the condition, because a fix no longer
    # implies the operator agreed to publish anything. With sharing off the
    # keys simply are not in the form.
    if f.get("advert_en") == "1":
        loc_note = (f"Map sharing ON: it will advertise a fuzzed point "
                    f"({f['advert_lat']}, {f['advert_lon']}), not where it is.")
    elif fix:
        loc_note = ("Map sharing OFF: its position was recorded on the medic "
                    "only, and the node advertises nothing.")
    else:
        loc_note = "No GPS fix — enter location manually or leave off."
    return StepResult(
        "wifi_onboarding", True, skipped=True,
        message=(
            f"Operator step: connect to WiFi '{ONBOARDING_SSID}', open "
            f"{ONBOARDING_URL}. Recommended LoRa settings are pre-filled — "
            f"freq {f['freq']} MHz, bandwidth {f['bw']} Hz, SF{f['sf']}, "
            f"CR{f['cr']}, {f['txp']} dBm. {loc_note} You still need to enter "
            f"the node name and WiFi SSID/password. Dismiss the portal after."))


@rtnode_build_step
def verify_beacon(wf: "RTNodeBuildWorkflow") -> StepResult:
    # Runs AFTER onboarding: only a configured board reaches health_beacon_init()
    # and fires its first beacon ~30 s after boot. We RESET the board over
    # serial and capture its boot log — the init line carries the health dst,
    # and the first announce (~30 s in) carries the payload.
    #
    # (Until 2026-07-30 this ran ``rnm-serial-capture`` — a command that does
    # not EXIST on the medic. verify_beacon failed on every birth ever run,
    # marking successful builds as failures. Believe outcomes: a LAN /status
    # answer is accepted as the fallback proof of life.)
    port = wf.profile.connection_port or wf.board_port or ""
    log = ""
    if port:
        log = wf.connection.run(serial_capture_cmd(port), timeout=75)[1] or ""
    m = _BEACON_RE.search(log)
    if m:
        dest_hash, data_hex = m.group(1), m.group(2)
        try:
            beacon = decode(bytes.fromhex(data_hex))
        except ValueError:
            return StepResult("verify_beacon", False,
                              "Beacon payload could not be decoded.")
        wf.beacon = beacon
        wf.profile.reticulum_identity_hash = dest_hash
        return StepResult("verify_beacon", True,
                          f"Board is beaconing: {beacon.board_label}, fw "
                          f"{beacon.firmware_version}, id {dest_hash[:12]}...")
    mi = _INIT_RE.search(log)
    if mi:
        # Boot banner seen with the health dst but the first announce didn't
        # land inside the capture window — the identity is still ground truth.
        wf.profile.reticulum_identity_hash = mi.group(1)
        return StepResult("verify_beacon", True,
                          f"Board booted healthy; health identity "
                          f"{mi.group(1)[:12]}... (first announce follows "
                          "within a minute — watch VITALS).")
    cfg, who = board_configured_on_lan(wf)
    if cfg:
        return StepResult("verify_beacon", True,
                          f"Serial was quiet but the board is alive on the "
                          f"LAN — {who}. Its beacon will appear on VITALS.")
    return StepResult("verify_beacon", False,
                      "No health beacon yet — has the board been onboarded "
                      "via the portal and rebooted? A fresh board stays "
                      "silent in setup mode. Verify over the mesh if USB is "
                      "quiet.")


@rtnode_build_step
def verify_sd_overflow(wf: "RTNodeBuildWorkflow") -> StepResult:
    """For SD-overflow targets (T-Beam Supreme), confirm the card mounted via the
    node's GET /status ``sd_overflow`` object — USB-free, same idea as the beacon
    check. Non-SD targets skip. The node is only reachable once onboarded onto
    WiFi, so without an address this defers with guidance rather than failing."""
    if wf.target.verify != "sd_status":
        return StepResult("verify_sd_overflow", True,
                          "Not an SD-overflow node — no SD tier to verify.",
                          skipped=True)
    if not wf.node_address:
        return StepResult(
            "verify_sd_overflow", True,
            "SD tier verifies via GET /status once the node is onboarded onto "
            "WiFi — set the node's address (mDNS name or IP) and re-check.",
            skipped=True)
    out = wf.connection.run(f"curl -s -m 5 http://{wf.node_address}/status")[1]
    ok, detail = check_sd_overflow(out)
    return StepResult("verify_sd_overflow", ok, detail)


@rtnode_build_step
def birth_certificate(wf: "RTNodeBuildWorkflow") -> StepResult:
    r = wf.profile.radio
    # Exact, un-fuzzed coordinates — ground truth for a repair visit. The public
    # map never sees these: the MEDIC fuzzes by ~800 m before the coordinates
    # ever leave here (monitor.geo.fuzz_location — this comment used to credit
    # the firmware, which was the exact mistake the 2026-08-01 audit found), and
    # the firmware then adds its own deterministic ~500 m on top. So a public
    # pin sits up to ~1.3 km from the hardware: monitor.geo.public_pin_radius_m.
    location = None
    if wf.gps_fix is not None:
        location = {"lat": wf.gps_fix.lat, "lon": wf.gps_fix.lon,
                    "source": wf.gps_fix.source,
                    # THE DECISION, NEXT TO THE COORDINATES IT GOVERNS. Whoever
                    # reads this certificate later — possibly not the person who
                    # built the node — can see whether the node is telling the
                    # world roughly where it is, without reading its flash.
                    "share_location": wf.profile.share_location}
    wf.birth_certificate = {
        "board": wf.beacon.board_label if wf.beacon else wf.profile.hardware.value,
        "firmware": wf.beacon.firmware_version if wf.beacon else None,
        "identity_hash": wf.profile.reticulum_identity_hash,
        "serial_port": wf.profile.connection_port,
        "build_env": wf.target.build_env,
        "frequency_mhz": r.frequency_mhz,
        "bandwidth_khz": r.bandwidth_khz,
        "spreading_factor": r.spreading_factor,
        "location": location,          # exact coords, or None if no GPS fix
        "session_id": wf.profile.session_id,
    }
    return StepResult("birth_certificate", True,
                      "Birth certificate ready (photograph / share via Bluetooth).")


class RTNodeBuildWorkflow:
    def __init__(self, connection: Connection, profile: NodeProfile,
                 gps_reader=None, target: str = DEFAULT_TARGET,
                 board_port: Optional[str] = None, node_name: str = "",
                 auto_provision: bool = False, provision=None,
                 wifi_credentials=None, join_ap=None, post=None, rejoin=None):
        self.connection = connection
        self.profile = profile
        #: The operator's chosen node name — flows into the portal /save form so the
        #: board itself takes the name (not just the medic's records).
        self.node_name = node_name
        #: When True, wifi_onboarding AUTO-provisions over the RTNode-Setup AP
        #: (join -> POST /save -> rejoin the medic's WiFi) instead of just printing
        #: instructions. Off by default (tests + a bare workflow don't hop WiFi);
        #: the real factory turns it on with live nmcli/HTTP functions.
        self.auto_provision = auto_provision
        self._provision = provision            # injected: rtnode_portal.provision_node
        self._wifi_credentials = wifi_credentials  # () -> (ssid, psk)
        self._join_ap = join_ap
        self._post = post
        self._rejoin = rejoin
        #: The WORK board's serial port, pinned by the caller (via
        #: local_board_ports, which EXCLUDES the medic's own onboard radio). When
        #: set, detect_board uses it instead of naively taking the first
        #: /dev/ttyACM* — which on the medic is ttyACM0 = Jonesey, its own radio.
        self.board_port = board_port
        # gps_reader() -> (lat, lon) | None. Injected for tests; None uses the
        # default gpsd reader at run time.
        self.gps_reader = gps_reader
        #: Which board to build (selects the PlatformIO env + verify strategy).
        self.target: RTNodeTarget = (
            RTNODE_TARGETS[target] if isinstance(target, str) else target)
        #: SD-overflow nodes verify over HTTP once onboarded; set to the node's
        #: mDNS name or IP when known.
        self.node_address: Optional[str] = None
        self.steps: List[Tuple[str, Callable]] = list(_RTNODE_STEPS)
        self.current_index = 0
        self.results: List[StepResult] = []
        self.beacon: Optional[HealthBeacon] = None
        self.gps_fix: Optional[GpsFix] = None
        self.onboarding: Optional[dict] = None
        self.birth_certificate: Optional[dict] = None

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        while self.current_index < len(self.steps):
            _, func = self.steps[self.current_index]
            result = func(self)
            self.results.append(result)
            # Every step lands in the (unbuffered) app log — a failed birth was
            # undiagnosable remotely for a whole day without this.
            from workflows.step_log import log_step
            log_step(result)
            emit(result)
            if not result.success and not result.skipped:
                break
            self.current_index += 1
        return self.results
