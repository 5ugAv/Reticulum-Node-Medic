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

_RTNODE_STEPS: List[Tuple[str, Callable]] = []


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
    # THROTTLE the compile: only 2 of the Pi's 4 cores, at low priority (nice 15),
    # so it can never overload the medic — the touchscreen stays smooth AND the live
    # radio (rnsd), GPS splitter and mesh keep running during a flash. A bit slower
    # than racing all cores, but the system stays responsive (progress ring fills
    # smoothly instead of freezing). Timeout raised to 900s to allow the gentler pace.
    cmd = (f"cd {RTNODE_PROJECT_DIR} && "
           f"nice -n 15 pio run -j 2 -e {wf.target.build_env} "
           f"-t upload --upload-port {port}")
    code, out, err = wf.connection.run(cmd, timeout=900)
    ok = code == 0
    return StepResult("flash_firmware", ok,
                      f"Flashed RTNode-2400 firmware ({wf.target.display})." if ok
                      else f"Flash failed: {err or out}")


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
        ok, msg = wf._provision(wf.profile, wf.node_name, ssid, psk, lat=lat, lon=lon,
                                join_ap=wf._join_ap, post=wf._post, rejoin=wf._rejoin)
        return StepResult("wifi_onboarding", ok, msg)

    wf.onboarding = build_form(wf.profile, node_name=wf.node_name, lat=lat, lon=lon)
    f = wf.onboarding
    loc_note = (f"GPS captured ({f['advert_lat']}, {f['advert_lon']}) — "
                f"advertised fuzzed on the public map."
                if fix else "No GPS fix — enter location manually or leave off.")
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
    # and fires its first beacon ~30 s after the post-onboarding reboot. Capture
    # ~45-60 s from that reboot. (A fresh board is silent — that's not a fault.)
    log = wf.connection.run(CAPTURE_COMMAND, timeout=60)[1]
    m = _BEACON_RE.search(log)
    if not m:
        return StepResult("verify_beacon", False,
                          "No health beacon yet — has the board been onboarded "
                          "via the portal and rebooted? A fresh board stays "
                          "silent in setup mode. Verify over the mesh if USB is "
                          "quiet.")
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
    # Exact, un-fuzzed coordinates — ground truth for a repair visit (the public
    # map only ever sees the firmware's ~800 m-fuzzed pin).
    location = None
    if wf.gps_fix is not None:
        location = {"lat": wf.gps_fix.lat, "lon": wf.gps_fix.lon,
                    "source": wf.gps_fix.source}
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
            emit(result)
            if not result.success and not result.skipped:
                break
            self.current_index += 1
        return self.results
