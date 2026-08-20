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
    """A board the RTNode-2400 build path can flash. The ESP32-S3 pair build via
    PlatformIO and verify over the air; the nRF52 T-Echo builds via arduino-cli,
    flashes over serial DFU, and is configured over USB (it has no WiFi radio,
    so the portal onboarding cannot exist for it)."""
    key: str
    display: str
    build_env: str
    hardware: "NodeHardware"
    verify: str = "beacon"      # "beacon" | "sd_status" | "eeprom" (USB read-back)
    mechanism: str = "pio"      # "pio" (pio run -t upload) | "nrf_dfu"
    # nrf_dfu targets only — the per-board identities and provisioning bytes.
    # usb_app_id: substring of the RUNNING app's by-id name (set by the
    #   Makefile's USB identity overrides). usb_boot_id: the bootloader's,
    #   burned in and unaffected by app flashes. flash_target: the Makefile
    #   flash rule. provision_args: rnodeconf -r bytes from the firmware's own
    #   Boards.h, never guessed.
    usb_app_id: str = ""
    usb_boot_id: str = ""
    #: by-id substrings the board presents BEFORE its first RTNode flash
    #: (stock firmware / generic core names) — used only to FIND the port.
    usb_stock_ids: tuple = ()
    flash_target: str = ""
    provision_args: str = ""


#: Operator-selected RTNode-2400 targets. Verified against platformio.ini on
#: branch feature/neopixel-status-led (commit 88d9aaf): both envs exist; the
#: Supreme carries the SD-overflow transport tier (-DFILESYSTEM_SD_OVERFLOW=1),
#: verified via its GET /status sd_overflow object rather than a health beacon.
RTNODE_TARGETS = {
    "heltec_v4": RTNodeTarget(
        "heltec_v4", "Heltec V4", RTNODE_BUILD_ENV,
        NodeHardware.HELTEC_V4, verify="beacon"),
    # V3 and V4 are both ESP32-S3, but they are NOT indistinguishable: the V3
    # goes through a CP2102 bridge (ttyUSB*) and the V4 is native USB (ttyACM*),
    # so ui.board_detect narrows an S3-on-a-bridge to the V3 alone. Proven on the
    # bench 2026-08-18 with both boards. V3 has its own PlatformIO env
    # (heltec_V3_boundary:
    # heltec_wifi_lora_32_V3 board, 8MB, no NeoPixel — V3 has no onboard RGB).
    "heltec_v3": RTNodeTarget(
        "heltec_v3", "Heltec V3", "heltec_V3_boundary",
        NodeHardware.HELTEC_V3, verify="beacon"),
    "tbeam_supreme": RTNodeTarget(
        "tbeam_supreme", "T-Beam Supreme (SD transport node)",
        "tbeam_supreme_boundary-local", NodeHardware.TBEAM_SUPREME,
        verify="sd_status"),
    # PROVEN BOOTING AND PROVISIONED 2026-08-19 (techo-support 5e1d4b9): the
    # noalloc image — RNS_USE_ALLOCATOR crashes this board before main(), so
    # the allocator-less build is the one that ships until the init order is
    # fixed properly. build_env here names the MAKE target in TECHO_PROJECT_DIR,
    # not a PlatformIO env; mechanism selects the whole different pipeline.
    "techo": RTNodeTarget(
        "techo", "LilyGO T-Echo", "firmware-techo-noalloc",
        NodeHardware.TECHO, verify="eeprom", mechanism="nrf_dfu",
        usb_app_id="T-Echo_RTNode-2400", usb_boot_id="LilyGo_T-Echo",
        usb_stock_ids=("Nordic",),
        flash_target="flash-techo VARIANT=noalloc",
        provision_args="--product 15 --model 17 --hwrev 1"),
    # PROVEN COMPILING 2026-08-20 (614308B/75%, 39036B/16% RAM) on the RAK
    # BSP installed with the native-ARM64 toolchain trick. Same nRF52840 +
    # SX1262 family as the T-Echo, so every hard-won fix (per-write eeprom
    # sync, firmware hash at birth, beacon port, noise-floor deadlock) rides
    # in from the shared tree. Model 0x12 = 779–928 MHz (915.125 fits, 22 dBm
    # cap — we run 17). The WisBlock ecosystem takes solar + real batteries:
    # this is the INSTALLABLE transport where the T-Echo is the carryable one.
    "rak4631": RTNodeTarget(
        "rak4631", "RAK4631", "firmware-rak4631-noalloc",
        NodeHardware.RAK4631, verify="eeprom", mechanism="nrf_dfu",
        # OBSERVED ON THE BENCH, not guessed (the first guess cost a failed
        # birth, 2026-08-20): the running app is manufacturer "RAKwireless"
        # (lowercase w), the bootloader "RAKWireless" (capital W) — same
        # product string, one capital apart, the LilyGo/LilyGO lesson again.
        # Names this fragile don't decide anything: the bootloader check is
        # by PID (8029 app vs 002a/0029 boot); these strings only FIND ports.
        usb_app_id="RAK4631_RTNode-2400", usb_boot_id="RAKWireless_WisBlock",
        usb_stock_ids=("RAKwireless_WisBlock",),
        flash_target="flash-rak4631 VARIANT=noalloc",
        provision_args="--product 10 --model 12 --hwrev 1"),
}
DEFAULT_TARGET = "heltec_v4"


#: Detector/catalogue board keys (``workflows.rnode_boards``, ``ui.board_detect``)
#: -> RTNODE_TARGETS keys. The two namespaces disagree by history, not by
#: hardware: "heltec32_v3" and "heltec_v3" are the same physical board. Keep the
#: crossing HERE — comparing the two vocabularies by display name is what made
#: every finished RTNode build pop "Not available for this board" (2026-08-18).
_DETECT_KEY_TO_TARGET = {
    "heltec32_v3": "heltec_v3",
    "heltec32_v4": "heltec_v4",
    # "techo" and "tbeam_supreme" are already target keys; they pass through.
}


def target_for_board_key(key):
    """The RTNODE_TARGETS key for a detector/catalogue board key.

    ``None`` when the board has no RTNode-2400 build, and also when ``key`` is
    falsy — an unidentified board is not a board we know cannot be built, and
    callers must not treat "don't know" as "no".
    """
    if not key:
        return None
    k = _DETECT_KEY_TO_TARGET.get(key, key)
    return k if k in RTNODE_TARGETS else None


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

#: The T-Echo builds from the techo-support tree (arduino-cli + Makefile), NOT
#: the PlatformIO tree above — every PlatformIO nRF52 env in that tree fails on
#: missing Arduino auto-prototypes (established 2026-08-18, commit 79ea489).
TECHO_PROJECT_DIR = "~/RTNode-2400"

#: Provisioning bytes from the firmware's own Boards.h — never guessed (the V4
#: lesson: a wrong model capped TX power). PRODUCT_TECHO 0x15, MODEL_17 0x17
#: (868/915 MHz band), hwrev 1. Verified against the tree 2026-08-19.
TECHO_PROVISION_ARGS = "--product 15 --model 17 --hwrev 1"
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

#: The identity line the nRF52 RTNode prints when RNS starts (added to the
#: firmware 2026-08-19 — FIREWALL_MODE targets announce theirs via the
#: HealthBeacon init line above, but the T-Echo build compiles neither WiFi
#: nor the firewall feature set, so this print is the only place its identity
#: crosses the USB boundary). identity = what every announce verifiably
#: carries (the registry's kin-fold key); dst = the rnstransport destination.
_TECHO_ID_RE = re.compile(
    r"\[RTNode\] identity=([0-9a-fA-F]+) dst=([0-9a-fA-F]+)")

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

    if wf.target.mechanism == "nrf_dfu":
        return _flash_techo(wf, port)

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


def _techo_raw_port(wf) -> str:
    """The T-Echo's RAW /dev/ttyACMx, resolved at the moment of use.

    NEVER hand rnodeconf a by-id symlink on an nRF52 board: its bootstrap
    closes the port mid-flow and rescans by USB serial number, and a symlink
    resolves that to None — which then matches the first port with NO serial,
    /dev/ttyAMA10, the medic's own UART. The whole provisioning then goes into
    the host's serial port while reporting success (proven with a wire spy,
    2026-08-19). ESP32 paths never hit this; nRF52 paths always must resolve.
    """
    # An UNMATCHED glob passes through bash as a literal, and GNU readlink -f
    # happily prints the literal and exits 0 — so a naive readlink-over-globs
    # returns the string "/dev/serial/by-id/*Nordic*" as a "port" whenever
    # other by-id devices exist but the T-Echo doesn't (adversarial review,
    # 2026-08-19). Existence-check every candidate; take only real nodes.
    ports = _nrf_ports(wf)
    if len(ports) > 1:
        # Two nRF-identity devices at once: refusing beats provisioning the
        # wrong one — the guard checked the PINNED port, not whatever a glob
        # happens to list first.
        return ""
    return ports[0] if ports else ""


def _techo_wait(wf, pattern: str, tries: int = 30) -> bool:
    """Wait for a USB identity to (re)appear — the board re-enumerates on every
    DFU entry, flash, and rnodeconf reset, and its ttyACM number can move.

    CASE-SENSITIVE on purpose. The bootloader is "LilyGo_T-Echo_v1" and the
    running app is "LilyGO_T-Echo_RTNode-2400" — one capital letter apart. A
    -qi grep read a running board as already-in-the-bootloader, skipped the
    touch, and the flash then refused with the board plugged in (review,
    2026-08-19)."""
    for _ in range(tries):
        if wf.connection.run(f"ls /dev/serial/by-id/ 2>/dev/null | grep -q '{pattern}'")[0] == 0:
            return True
        _sleep(2)
    return False


#: The two USB identities, one capital apart. The app identity is set by the
#: Makefile's USB_ID overrides (manufacturer LilyGO, product "T-Echo
#: RTNode-2400"); the bootloader's is burned into it and unaffected by any app
#: flash. Distinguishing them is what makes the touch decision correct.
TECHO_BOOTLOADER_ID = "LilyGo_T-Echo"
TECHO_APP_ID = "T-Echo_RTNode-2400"


def _nrf_ports(wf):
    """Every real port matching this target's identities, resolved NOW."""
    t = wf.target
    pats = [p for p in (t.usb_app_id, t.usb_boot_id, *t.usb_stock_ids) if p]
    globs = " ".join(f"/dev/serial/by-id/*{p}*" for p in pats)
    out = wf.connection.run(
        f"for f in {globs}; do "
        "[ -e \"$f\" ] && readlink -f \"$f\"; done 2>/dev/null | sort -u")[1]
    return [l.strip() for l in out.splitlines() if l.strip().startswith("/dev/")]


def _nrf_reresolve(wf, step: str):
    """(port, failure StepResult|None) — the ONLY way a step may re-find its
    board mid-flow. The old pattern was `_techo_raw_port(wf) or raw`: the
    resolver's own >1-boards refusal came back as "" and the `or raw` erased
    it, falling back to a STALE tty — and in emulation a second board that
    took that tty number during a reset received the birth board's firmware
    hash and TNC params while the step reported success (storm review,
    2026-08-20). None and many are different truths and neither may ever
    become "use the old number"."""
    ports = _nrf_ports(wf)
    if len(ports) == 1:
        return ports[0], None
    if not ports:
        return "", StepResult(step, False,
                              "The board is not on USB right now (it may "
                              "still be rebooting) — refusing to touch the "
                              "old port number, which another device could "
                              "have taken. Run the build again.")
    return "", StepResult(step, False,
                          f"{len(ports)} boards match — unplug the others; "
                          "provisioning refuses to guess which one is being "
                          "born.")


def _kiss_ready(wf, raw: str, timeout: int = 90) -> bool:
    """Wait until the firmware ANSWERS — USB enumeration is not readiness.

    On nRF52 the KISS handler only runs once setup() finishes, and a
    first-ever boot formats LittleFS in setup — tens of seconds during which
    the port exists and says nothing. rnodeconf waits ~3s and declares "RNode
    did not respond" about a board that is busy being born (RAK4631 maiden
    birth, 2026-08-20; the T-Echo escaped only because its filesystem had
    been formatted during pre-birth debugging). The probe succeeds only on
    the firmware's own CMD_DETECT response."""
    return wf.connection.run(
        f"python3 ~/reticulum-tool/scripts/kiss_detect.py {raw} {timeout}",
        timeout=timeout + 15)[0] == 0


def _flash_techo(wf, port: str) -> StepResult:
    """Build + DFU-flash the T-Echo: make in the techo tree, 1200-baud touch
    into the bootloader (no hands — proven on this board), then the Makefile's
    guarded flash-techo target, which finds the board by its LilyGo identity
    and refuses if that ever resolves to the medic's own radio."""
    # set -o pipefail everywhere a pipe feeds the exit-code check: bash -c
    # without it returns TAIL's status, which is always 0, and every failure
    # check downstream of a pipe was dead (adversarial review, 2026-08-19).
    build = wf.connection.run(
        f"set -o pipefail; cd {TECHO_PROJECT_DIR} && "
        f"export PATH=$HOME/.local/bin:$PATH && "
        f"nice -n 15 make {wf.target.build_env} 2>&1 | tail -3", timeout=1200)
    if build[0] != 0:
        return StepResult("flash_firmware", False,
                          "T-Echo firmware build failed: "
                          f"{(build[1] or '').strip()[-300:] or 'build timed out'}")
    t = wf.target
    # THE MULTI-BOARD REFUSAL RUNS FIRST — before the bootloader-skip decision.
    # It used to live only on the touch branch, so two nRF boards with one
    # already in DFU sailed past it straight into the Makefile's head -1 glob
    # (prosecution review, 2026-08-20). _techo_raw_port returns "" on >1.
    raw = _techo_raw_port(wf)
    # IN-BOOTLOADER IS A PID QUESTION. On the RAK4631 the app and bootloader
    # by-id names differ by one capital letter; the first birth read the
    # RUNNING APP as "already in bootloader", skipped the touch, and aimed
    # DFU at an application — which answered garbage ("Bootloader version
    # does not match", 2026-08-20). The USB PID cannot be spoofed by a
    # naming coincidence: 0x0029/0x002a/0x0071 are the UF2 bootloaders.
    in_boot = False
    if raw:
        pid = wf.connection.run(
            f"udevadm info -q property -n {raw} 2>/dev/null "
            f"| grep '^ID_MODEL_ID=' | cut -d= -f2")[1].strip().lower()
        from workflows.rnode_flash import NRF_DFU_PIDS
        in_boot = pid in NRF_DFU_PIDS
    if not raw and not in_boot:
        return StepResult("flash_firmware", False,
                          f"No {t.display} on USB (or more than one nRF board "
                          "attached — unplug the others). Plug it in with "
                          "a data cable and build again.")
    # Already in the bootloader (double-tapped by hand)? Then skip the touch.
    # EXACT case: the RUNNING app is one capital away on the T-Echo.
    if not in_boot:
        wf.connection.run(
            f"python3 ~/reticulum-tool/scripts/techo_touch.py {raw}",
            timeout=30)
        if not _techo_wait(wf, t.usb_boot_id, tries=15):
            return StepResult("flash_firmware", False,
                              "The 1200-baud touch didn't reach the bootloader "
                              "— double-tap the side RESET button (two quick "
                              "presses) and build again.")
    # ONE whole-transfer retry lap, never a mid-stream one: resent DFU packets
    # confuse the bootloader's state machine (Columba's war note), so the only
    # safe retry is the entire DFU after re-confirming the bootloader is there.
    flash = ("", "", "")
    for attempt in (1, 2):
        flash = wf.connection.run(
            f"set -o pipefail; cd {TECHO_PROJECT_DIR} && "
            f"export PATH=$HOME/.local/bin:$PATH && "
            f"make {t.flash_target} 2>&1 | tail -4", timeout=300)
        if flash[0] == 0 and "programmed" in (flash[1] or "").lower():
            break
        if attempt == 1 and not _techo_wait(wf, t.usb_boot_id, tries=10):
            break                       # bootloader gone — a retry can't help
    if flash[0] != 0 or "programmed" not in (flash[1] or "").lower():
        return StepResult("flash_firmware", False,
                          f"DFU flash failed: {(flash[1] or '').strip()[-300:]}")
    # Wait for the APP identity, exactly — the loose "T-Echo" pattern also
    # matched the bootloader's name, so a board that crashed straight back
    # into DFU read as "booted and talking" (prosecution review, 2026-08-20).
    if not _techo_wait(wf, t.usb_app_id, tries=15):
        return StepResult("flash_firmware", False,
                          "Flashed, but the board did not come back as the "
                          "running app — check the cable, then double-tap "
                          "RESET and retry.")
    fresh, fail = _nrf_reresolve(wf, "flash_firmware")
    if fail:
        return fail
    wf.profile.connection_port = fresh
    wf.profile.radio.serial_port = fresh
    if not _kiss_ready(wf, fresh):
        return StepResult("flash_firmware", False,
                          "The board enumerated but never answered the RNode "
                          "detect — the firmware may have hung during its "
                          "first boot. Replug it and run the build again.")
    return StepResult("flash_firmware", True,
                      f"Flashed RTNode-2400 ({wf.target.display}, serial DFU) "
                      "and the board booted — back on USB and answering.")


def _onboard_techo(wf) -> StepResult:
    """The T-Echo's onboarding is over USB — it has no WiFi radio, so the
    captive portal cannot exist for it. Two rnodeconf passes: bootstrap the
    EEPROM (identity, signature), then bake the canonical radio parameters in
    TNC mode so the node runs them standalone (set-radio-params-at-birth)."""
    raw, fail = _nrf_reresolve(wf, "wifi_onboarding")
    if fail:
        return fail
    if not _kiss_ready(wf, raw):
        return StepResult("wifi_onboarding", False,
                          "The board is on USB but not answering the RNode "
                          "detect — a first boot can take a while (it formats "
                          "its filesystem); run the build again.")
    r = wf.profile.radio
    prov = wf.connection.run(
        f"set -o pipefail; export PATH=$HOME/.local/bin:$PATH && "
        f"timeout 150 rnodeconf {raw} -r {wf.target.provision_args} 2>&1 | tail -4",
        timeout=180)
    text = prov[1] or ""
    if "EEPROM checksum mismatch" in text:
        # The one state -r cannot pass THROUGH: a locked-but-corrupt EEPROM
        # (our own 2026-08-19 bench residue was exactly this). rnodeconf
        # refuses — with exit code 0, its refusals always exit 0 — and no
        # amount of rerunning helps; the cure is a wipe. The store is
        # unreadable, so its identity is already lost: wiping loses nothing
        # that exists, and saying so is the honest note. One wipe, one retry.
        wf.connection.run(
            f"export PATH=$HOME/.local/bin:$PATH && "
            f"timeout 90 rnodeconf {raw} --eeprom-wipe 2>&1 | tail -2",
            timeout=120)
        if not _techo_wait(wf, wf.target.usb_app_id, tries=20):
            return StepResult("wifi_onboarding", False,
                              "The stored EEPROM was corrupt; it was wiped "
                              "but the board did not return — double-tap "
                              "RESET and run the build again.")
        raw, fail = _nrf_reresolve(wf, "wifi_onboarding")
        if fail:
            return fail
        if not _kiss_ready(wf, raw):
            return StepResult("wifi_onboarding", False,
                              "Wiped a corrupt EEPROM but the board is not "
                              "answering yet — run the build again.")
        prov = wf.connection.run(
            f"set -o pipefail; export PATH=$HOME/.local/bin:$PATH && "
            f"timeout 150 rnodeconf {raw} -r {wf.target.provision_args} "
            f"2>&1 | tail -4", timeout=180)
        text = prov[1] or ""
        wf.techo_eeprom_wiped = True
    if "Bootstrapping successful" not in text:
        # (The old check also accepted any line containing "successful" —
        # which matches "unsuccessful" too. One exact sentence, or the
        # read-back decides.) A previously provisioned board REFUSES a
        # re-bootstrap and that is fine — its identity is meant to survive.
        chk_port, fail = _nrf_reresolve(wf, "wifi_onboarding")
        if fail:
            return fail
        chk = wf.connection.run(
            f"export PATH=$HOME/.local/bin:$PATH && "
            f"timeout 40 rnodeconf {chk_port} -i 2>&1", timeout=60)[1] or ""
        if "signature validated" not in chk.lower():
            # Quote the READ-BACK that made the decision, not -r's benign
            # "already present" refusal — a bad-signature board used to fail
            # under words that never said "signature" (storm review F6).
            wiped_note = (" A corrupt EEPROM was already wiped once this "
                          "run; if this repeats, the flash itself is "
                          "suspect — reflash, then build again."
                          if getattr(wf, "techo_eeprom_wiped", False) else "")
            return StepResult("wifi_onboarding", False,
                              f"EEPROM read-back failed validation: "
                              f"{chk.strip()[-300:]}{wiped_note}")
    # rnodeconf -r ends in a hard reset on nRF52 — the board re-enumerates and
    # its ttyACM number can move. WAIT for the APP identity (the loose family
    # pattern also matched the bootloader — prosecution review, 2026-08-20).
    if not _techo_wait(wf, wf.target.usb_app_id, tries=15):
        return StepResult("wifi_onboarding", False,
                          "Board did not return after provisioning — check "
                          "the cable, then run the build again (its identity "
                          "is already saved; the retry is safe).")
    raw, fail = _nrf_reresolve(wf, "wifi_onboarding")
    if fail:
        return fail
    if not _kiss_ready(wf, raw):
        return StepResult("wifi_onboarding", False,
                          "The board returned but is not answering yet — run "
                          "the build again (its identity is already saved).")
    # THE FIRMWARE HASH, WITHOUT WHICH EVERYTHING ELSE QUIETLY DIES. The
    # firmware compares its stored expected-hash against the running image at
    # boot; unset, the check fails, hw_ready stays false, RNS refuses to
    # start, the LEDs sit red, the e-paper says FIRMWARE CORRUPT — and
    # eeprom_conf_save() silently refuses too, so the TNC params set below
    # LOOK saved (rnodeconf prints its success from the host side) and are
    # not. The first real birth shipped exactly that board (2026-08-20,
    # diagnosed from its own boot log: "RNS is inoperable because hardware is
    # not ready"). The device computes its own running-image hash; we write
    # it back as the expectation — the same step upload-techo always had.
    hcmd = wf.connection.run(
        f"set -o pipefail; cd {TECHO_PROJECT_DIR} && "
        f"export PATH=$HOME/.local/bin:$PATH && "
        f"timeout 80 ./partition_hashes from_device {raw} 2>/dev/null | tail -1",
        timeout=90)
    fw_hash = (hcmd[1] or "").strip().splitlines()[-1].strip() if (hcmd[1] or "").strip() else ""
    if fw_hash == "0" * 64:
        # Columba's war table: a device mid-glitch returns all zeros, and
        # writing THAT as the expectation bricks hw_ready on the next boot —
        # they log a warning and write it anyway; we refuse.
        return StepResult("wifi_onboarding", False,
                          "The board reported an all-zeros firmware hash — "
                          "that is a read glitch, not a hash. Replug and "
                          "build again.")
    if len(fw_hash) != 64 or not all(c in "0123456789abcdef" for c in fw_hash):
        return StepResult("wifi_onboarding", False,
                          "Could not read the running firmware's hash off the "
                          f"board: {(hcmd[1] or '').strip()[-200:] or 'no output'}")
    seth = wf.connection.run(
        f"set -o pipefail; export PATH=$HOME/.local/bin:$PATH && "
        f"timeout 60 rnodeconf {raw} --firmware-hash {fw_hash} 2>&1 | tail -3",
        timeout=90)
    if "Firmware hash set" not in (seth[1] or ""):
        return StepResult("wifi_onboarding", False,
                          f"Setting the firmware hash failed: "
                          f"{(seth[1] or '').strip()[-200:]}")
    raw, fail = _nrf_reresolve(wf, "wifi_onboarding")
    if fail:
        return fail
    freq_hz = int(round(r.frequency_mhz * 1_000_000))
    bw_hz = int(r.bandwidth_khz * 1000)
    # -T (TNC mode) is the branch that CONSUMES the five flags and leaves the
    # node running them standalone. -N is normal/host-controlled mode and
    # SILENTLY IGNORES all five — the first version of this step used -N and
    # would have shipped an unconfigured node under a green success message
    # (adversarial review, 2026-08-19, verified in rnodeconf's source). With
    # all five flags present -T asks nothing; missing flags would prompt on a
    # terminal and hang a scripted run, so keep them all explicit.
    params = wf.connection.run(
        f"set -o pipefail; export PATH=$HOME/.local/bin:$PATH && "
        f"timeout 60 rnodeconf {raw} -T --freq {freq_hz} --bw {bw_hz} "
        f"--sf {r.spreading_factor} --cr {r.coding_rate} "
        f"--txp {r.tx_power_dbm} 2>&1 | tail -3", timeout=90)
    ptext = (params[1] or "")
    if params[0] != 0 or "TNC" not in ptext:
        return StepResult("wifi_onboarding", False,
                          f"Radio parameters failed to save: "
                          f"{ptext.strip()[-250:] or 'no response'}")
    # The GPS fix every other birth captures — the medic is physically at the
    # node right now, so its fix IS the node's location. SAME contract as the
    # portal path: wf.gps_fix, which birth_certificate reads; no fix is a
    # recorded absence, never a fake position. (JONESEY carries the GPS, so
    # while it is away every fix is honestly None.)
    try:
        fix = (read_gps(wf.gps_reader) if wf.gps_reader is not None
               else read_gps())
        wf.gps_fix = fix
    except Exception:
        wf.gps_fix = None
    # F3 (storm): the certificate must name the port the board LIVES on,
    # not the pin from two re-enumerations ago.
    final_port, _ = _nrf_reresolve(wf, "wifi_onboarding")
    if final_port:
        wf.profile.connection_port = final_port
        wf.profile.radio.serial_port = final_port
    if getattr(wf, "techo_eeprom_wiped", False):
        ident_word = ("stored EEPROM was corrupt — wiped and freshly "
                      "provisioned (a new identity; the old store was "
                      "unreadable)")
    elif "Bootstrapping successful" in text:
        ident_word = "identity provisioned"
    else:
        # the -r refusal path: the identity SURVIVED, which is the design —
        # but "provisioned" would claim a mint that never happened.
        ident_word = "identity already present — kept"
    return StepResult("wifi_onboarding", True,
                      f"Configured over USB (no WiFi on this board): "
                      f"{ident_word}, firmware hash set, radio set to "
                      f"{r.frequency_mhz} MHz SF{r.spreading_factor} "
                      f"CR{r.coding_rate} {r.tx_power_dbm} dBm — running "
                      f"standalone (TNC mode).")


def _verify_techo(wf) -> StepResult:
    """Read the provisioning back off the board over USB. Honest scope: this
    proves the EEPROM and signature, not over-the-air reach — say so, rather
    than implying a radio check that did not happen."""
    raw, fail = _nrf_reresolve(wf, "verify_beacon")
    if fail:
        return fail
    # FIRST: reboot the configured board and read its own words. This is the
    # start of identity-over-USB (2026-08-19): the firmware prints
    # "[RTNode] identity=… dst=…" the moment RNS comes up, and that identity
    # is what lets the certificate name the node and the kin roster fold its
    # announces from the first one heard. Best-effort — a board running older
    # firmware prints no such line, and that absence is recorded honestly
    # downstream, never invented.
    ident = dst = None
    bootlog = wf.connection.run(
        f"timeout 75 python3 ~/reticulum-tool/scripts/techo_bootlog.py "
        f"{raw} 40 2>&1", timeout=90)[1] or ""
    # THE BOARD'S OWN VERDICT OUTRANKS EVERY EEPROM READ. rnodeconf prints
    # "checksum correct / signature validated" from the EEPROM alone — a
    # firmware-hash mismatch kills hw_ready and RNS while rnodeconf keeps
    # answering politely, so the old check passed the exact shipped-dead
    # state of 2026-08-20 (prosecution review). The boot log names it.
    if "hardware is not ready" in bootlog:
        return StepResult("verify_beacon", False,
                          "The board's own boot log says RNS is inoperable — "
                          "hardware not ready. That is the firmware-hash / "
                          "provisioning gate failing on the device. Run the "
                          "build again; if it repeats, the flash and the "
                          "stored hash disagree.")
    m = _TECHO_ID_RE.search(bootlog)
    if m:
        ident, dst = m.group(1), m.group(2)
        wf.profile.reticulum_identity_hash = ident
        wf.techo_dst = dst
    raw, fail = _nrf_reresolve(wf, "verify_beacon")   # the reset moves the port
    if fail:
        return fail
    # Target-vs-actual firmware hash EQUALITY — the check the EEPROM read
    # cannot make. Both lines come from the device (rnodeconf -K -L prints
    # "The target firmware hash is:" / "The actual firmware hash is:"); if
    # they differ, the next boot is the dead state above, and saying so NOW
    # beats an operator discovering red LEDs in the field.
    kl = wf.connection.run(
        f"set -o pipefail; export PATH=$HOME/.local/bin:$PATH && "
        f"timeout 40 rnodeconf {raw} -K -L 2>&1", timeout=60)[1] or ""
    hashes = re.findall(r"hash is:\s*\n?\s*([0-9a-f]{64})", kl)
    if len(hashes) >= 2 and hashes[0] != hashes[1]:
        return StepResult("verify_beacon", False,
                          "Firmware hash mismatch: the stored expectation and "
                          "the running image disagree — the board will refuse "
                          "to start RNS on its next boot. Re-run the build "
                          "(the flash and hash steps repair this).")
    out = wf.connection.run(
        f"export PATH=$HOME/.local/bin:$PATH && "
        f"timeout 40 rnodeconf {raw} -i 2>&1", timeout=60)[1] or ""
    low = out.lower()
    if "signature validated" in low and "checksum correct" in low:
        # The certificate's firmware field comes from the beacon on other
        # targets; here the same fact is in the -i output. Read it rather
        # than printing "firmware: None" about an image we just flashed.
        m = re.search(r"Firmware version\s*:\s*([\w.\-]+)", out)
        if m:
            wf.techo_fw_version = m.group(1)
        if ident:
            return StepResult("verify_beacon", True,
                              f"Provisioning verified over USB: EEPROM "
                              f"checksum correct, signature validated, and "
                              f"the node announced its identity "
                              f"{ident[:12]}… at boot. (No over-the-air "
                              f"check — that needs the medic's own radio "
                              f"listening.)")
        return StepResult("verify_beacon", True,
                          "Provisioning verified over USB: EEPROM checksum "
                          "correct, device signature validated. This "
                          "firmware did not print its identity at boot "
                          "(older image) — the node will appear in VITALS "
                          "when first heard. (No over-the-air check — that "
                          "needs the medic's own radio listening.)")
    return StepResult("verify_beacon", False,
                      f"Board did not validate: {out.strip()[-250:]}")


@rtnode_build_step
def wifi_onboarding(wf: "RTNodeBuildWorkflow") -> StepResult:
    if wf.target.mechanism == "nrf_dfu":
        return _onboard_techo(wf)
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
    if wf.target.verify == "eeprom":
        return _verify_techo(wf)
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
        "firmware": (wf.beacon.firmware_version if wf.beacon
                     else getattr(wf, "techo_fw_version", None)),
        "identity_hash": wf.profile.reticulum_identity_hash,
        "serial_port": wf.profile.connection_port,
        "build_env": wf.target.build_env,
        "frequency_mhz": r.frequency_mhz,
        "bandwidth_khz": r.bandwidth_khz,
        "spreading_factor": r.spreading_factor,
        "location": location,          # exact coords, or None if no GPS fix
        "session_id": wf.profile.session_id,
    }
    if getattr(wf, "techo_dst", None):
        # The rnstransport destination the node announced at boot (nRF52
        # boot-log read) — the kin roster keys on BOTH hashes, so either
        # sighting folds it. Only present when actually read: the cert page
        # prints every field verbatim, and a permanent None key would put
        # "reticulum_address: None" on every ESP32 certificate.
        wf.birth_certificate["reticulum_address"] = wf.techo_dst
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

    @property
    def step_display(self):
        """Per-build names for the checklist rows. The step FUNCTIONS are
        shared across targets, but the T-Echo's onboarding is USB and its
        verify is an EEPROM read-back — showing "wifi_onboarding" on a board
        with no WiFi radio was a lie in the checklist (review, 2026-08-19)."""
        if self.target.mechanism == "nrf_dfu":
            return {"wifi_onboarding": "usb_setup",
                    "verify_beacon": "verify_usb"}
        return {}

    @property
    def step_seconds(self):
        """Per-build progress-ring weights. The global _STEP_SECONDS says
        wifi_onboarding=2s; a legal nRF usb_setup can run ~570s (first-boot
        LittleFS format + three rnodeconf conversations + two reboots) — the
        ring froze at 100% for minutes, the exact banned "stall" look
        (storm review F4). Estimates, honestly sized."""
        if self.target.mechanism == "nrf_dfu":
            return {"flash_firmware": 240, "wifi_onboarding": 300,
                    "verify_beacon": 120}
        return {}

    @property
    def step_phase_labels(self):
        """The live one-line narration per step, same rule as step_display."""
        if self.target.mechanism == "nrf_dfu":
            return {
                "flash_firmware": "Building and flashing over serial DFU… "
                                  "the board reboots twice — keep it plugged "
                                  "in.",
                "wifi_onboarding": "Configuring over USB — identity, then the "
                                   "radio parameters (no WiFi on this board; "
                                   "nothing leaves your network).",
                "verify_beacon": "Verifying over USB — reading the "
                                 "provisioning back off the board.",
            }
        return {}

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
