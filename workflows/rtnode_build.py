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

import re
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
#: PlatformIO build environment for the Heltec V4 RTNode-2400 target.
RTNODE_BUILD_ENV = "heltec_V4_boundary-local"
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
def detect_heltec_v4(wf: "RTNodeBuildWorkflow") -> StepResult:
    out = wf.connection.run(f"ls {' '.join(_PORT_GLOBS)} 2>/dev/null")[1]
    ports = out.split()
    if not ports:
        return StepResult("detect_heltec_v4", False,
                          "No board found — plug in the Heltec V4 (try another "
                          "USB-C cable; some are charge-only).")
    port = ports[0]
    # More than one USB-serial device present: don't silently guess.
    extra = ("" if len(ports) == 1 else
             f" WARNING: {len(ports)} USB-serial devices seen "
             f"({', '.join(ports)}); using {port}. Unplug the others to be sure "
             f"you flash the right board.")
    wf.profile.hardware = NodeHardware.HELTEC_V4
    wf.profile.connection_port = port
    wf.profile.radio.serial_port = port
    return StepResult("detect_heltec_v4", True,
                      f"Found Heltec V4 on {port}.{extra}")


@rtnode_build_step
def flash_firmware(wf: "RTNodeBuildWorkflow") -> StepResult:
    port = wf.profile.connection_port
    cmd = (f"cd {RTNODE_PROJECT_DIR} && "
           f"pio run -e {RTNODE_BUILD_ENV} -t upload --upload-port {port}")
    code, out, err = wf.connection.run(cmd, timeout=600)
    ok = code == 0
    return StepResult("flash_firmware", ok,
                      "Flashed RTNode-2400 firmware." if ok
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
    wf.onboarding = build_form(wf.profile, lat=lat, lon=lon)
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
        "build_env": RTNODE_BUILD_ENV,
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
                 gps_reader=None):
        self.connection = connection
        self.profile = profile
        # gps_reader() -> (lat, lon) | None. Injected for tests; None uses the
        # default gpsd reader at run time.
        self.gps_reader = gps_reader
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
