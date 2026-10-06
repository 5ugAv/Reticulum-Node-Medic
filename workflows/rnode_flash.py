"""Flash a supported board as a stock RNode — the RNode option under Birth.

Verified end-to-end on a real Heltec V4: a fresh board is flashed AND provisioned
entirely from the OFFLINE firmware cache by pre-feeding rnodeconf --autoinstall's
answers via stdin (device index -> enter -> band -> confirm). No interactive
terminal and no internet needed once the firmware cache is seeded.

Flow: detect the port -> refuse to guess between multiple boards -> ensure the
firmware is cached (sync when online, else use the carried cache) -> flash ->
verify the board reports as a provisioned RNode.
"""

from __future__ import annotations

import os
import re
import shlex
from typing import Callable, List, Optional

from provisioning.by_id import by_id_serial
from transport.connection import Connection
from workflows.build import StepResult, detect_rnode_port
from workflows.rnode_boards import RNodeBoard
from workflows.radio_params import set_params_at_birth
from workflows.updater import (
    autoinstall_command, sync_firmware, has_connectivity, RNODE_UPDATE_DIR)

#: Firmware bundle version the tool carries / targets.
FIRMWARE_VERSION = "1.86"


def esptool_path(version: str = FIRMWARE_VERSION) -> str:
    """Where rnodeconf caches esptool, alongside the firmware it downloads.

    THE SINGLE SOURCE. This path was hardcoded as a literal in three other
    modules (board_detect, robust_flash, rnode_v4_rgb) with the version baked
    in. Bump FIRMWARE_VERSION and those three keep pointing at a directory
    rnodeconf no longer populates — board detection and flashing both break,
    silently, in the field (audit, 2026-08-03). That bump is imminent: reading
    eFuses to tell the native-USB S3 boards apart needs esptool >= 4.7.
    """
    return f"~/.config/rnodeconf/update/{version}/esptool.py"


def esptool_cmd(version: str = FIRMWARE_VERSION) -> str:
    """The full `python3 <path>` invocation the callers actually use."""
    return f"python3 {esptool_path(version)}"
#: rnodeconf prints this once a device is flashed AND provisioned.
SUCCESS_MARKER = "autoinstallation complete"
#: rnodeconf refuses (exit 0) to re-flash an already-provisioned RNode.
ALREADY_PROVISIONED_MARKER = "already installed and provisioned"


def flash_command(board: RNodeBoard, port: str, band_mhz: int = 915,
                  version: str = FIRMWARE_VERSION) -> str:
    """The exact non-interactive offline flash command (verified on hardware):
    pre-feed the autoinstall answers via stdin into rnodeconf --autoinstall
    --nocheck. Raises ValueError if the board's sequence isn't verified for the
    requested band."""
    answers = board.autoinstall_answers(band_mhz)
    ans = " ".join(shlex.quote(a) for a in answers)
    inner = (f"printf '%s\\n' {ans} | "
             + autoinstall_command(port, version=version, offline=True))
    # rnodeconf's confirm prompts read a KEYPRESS FROM THE TERMINAL, not
    # stdin — a bare pipe blocks forever and wedges the USB port (proven on
    # the medic; the local path uses pexpect for this reason). On a remote
    # host we have no pexpect, so allocate a PTY with util-linux `script`
    # (present on Raspberry Pi OS) and fall back to the bare pipe if it's
    # missing (2026-08-01 bug hunt: the Pi+RNode blank-board path).
    return (f"if command -v script >/dev/null 2>&1; then "
            f"script -qec {shlex.quote(inner)} /dev/null; else {inner}; fi")


#: Prompt patterns rnodeconf --autoinstall shows, paired with the answer index
#: in board.autoinstall_answers() ([device_index, '', band, 'y']). The confirm
#: and "hit enter" prompts read a keypress from the TERMINAL (not stdin), so they
#: are driven through a PTY (connection.run_interactive), never a stdin pipe.
_AUTOINSTALL_PROMPTS = ("matches your device type", "Hit enter to continue",
                        "What band", "Is the above correct")


def autoinstall_interactions(board: RNodeBoard, band_mhz: int = 915):
    """``(regex, response)`` pairs that drive rnodeconf --autoinstall through its
    terminal prompts for *board*. Raises ValueError (via autoinstall_answers) if
    the board's flash sequence isn't verified for *band_mhz*."""
    answers = board.autoinstall_answers(band_mhz)
    return list(zip(_AUTOINSTALL_PROMPTS, answers))


def _autoinstall_ok(code: int, out: str) -> bool:
    low = out.lower()
    return (ALREADY_PROVISIONED_MARKER in low) or (SUCCESS_MARKER in low)


def _esp32_hw_cdc_touch(connection, port: str):
    """(port, note) — park a hardware-CDC ESP32 in its bootloader before
    rnodeconf ever opens the port.

    An ESP32-S3 running firmware with ARDUINO_USB_MODE=1 (our RTNode-2400
    images) presents the chip's OWN USB-JTAG/serial peripheral — and that
    peripheral RESETS THE CHIP when a program opens the port with DTR/RTS
    asserted, which pyserial does by default. rnodeconf holds one serial
    handle for its whole run, so its opening probe kills the board under it
    and every later serial write hits a dead fd: the XIAO reflash hung,
    then crashed at leave(), then 'Could not find specified port' —
    three faces of one cause (2026-08-20). In the ROM bootloader the port
    is stable and opening it is harmless, so: esptool performs its DTR/RTS
    download-mode dance and LEAVES the chip there (--after no_reset);
    autoinstall then probes a silent, stable port, takes the fresh-device
    path, and flashes. The nRF path has done exactly this (the 1200-baud
    touch) since its first birth.

    Only fires when udevadm says the port IS the hardware CDC
    ("USB_JTAG_serial_debug_unit"); stock TinyUSB boards (e.g. the factory
    XIAO) and bridge boards (CP2102/CH340) keep the proven direct path.
    Re-resolves the port by USB serial number afterwards — entering the
    bootloader can re-enumerate it."""
    try:
        model = (connection.run(
            f"udevadm info -q property -n {port} 2>/dev/null | "
            f"grep '^ID_MODEL=' | cut -d= -f2")[1] or "").strip()
        if model != "USB_JTAG_serial_debug_unit":
            return port, ""
        serial = (connection.run(
            f"udevadm info -q property -n {port} 2>/dev/null | "
            f"grep '^ID_SERIAL_SHORT=' | cut -d= -f2")[1] or "").strip()
        # esptool.py, the pip entry point — a bare `esptool` does not exist
        # on the medic (the first deploy of this touch failed open on that
        # and the reflash raced the reset exactly as before, 2026-08-20).
        # The park must be VERIFIED, not assumed: no esptool -> no touch ->
        # say so, rather than letting rnodeconf race the reset again.
        # rc captured BEFORE the settle sleep — a trailing command would
        # replace the exit code and make this check dead (the pipefail
        # lesson, again).
        code = connection.run(
            f"export PATH=$HOME/.local/bin:$PATH && "
            f"timeout 30 esptool.py --port {port} --after no_reset "
            f"chip_id >/dev/null 2>&1; rc=$?; sleep 2; exit $rc",
            timeout=45)[0]
        if code != 0:
            return port, ("WARNING: could not park the board in its "
                          "bootloader (esptool.py missing or the touch "
                          "failed) — the flash may race the board's "
                          "reset-on-open")
        if serial:
            fresh = (connection.run(
                "for f in /dev/serial/by-id/*" + serial + "*; do "
                "[ -e \"$f\" ] && readlink -f \"$f\"; done 2>/dev/null "
                "| head -1")[1] or "").strip()
            if fresh.startswith("/dev/"):
                return fresh, "parked in the bootloader for a stable flash"
        return port, "parked in the bootloader for a stable flash"
    except Exception:                                  # noqa: BLE001
        return port, ""                               # fail open — old path


#: The flasher rnodeconf shells out to for every nRF52 board. Named here so the
#: preflight and any future carrier of it agree on one string.
NRF_FLASHER = "adafruit-nrfutil"


def _missing_nrf_flasher(connection):
    """Empty string if the nRF52 flasher is present, else why not and the fix.

    Checked through the CONNECTION, not the tool host: on the cable-birth path
    the flash runs on the medic, and what matters is whether the binary is on
    the PATH of whatever will actually run rnodeconf.
    """
    try:
        code, out, _err = connection.run(f"command -v {NRF_FLASHER}", timeout=20)
    except Exception:                                  # noqa: BLE001
        return ""                                      # cannot tell — let it try
    if code == 0 and (out or "").strip():
        return ""
    return (f"{NRF_FLASHER} is not installed, and nRF52 boards cannot be "
            f"flashed without it (rnodeconf calls it to write the DFU). "
            f"Install it with:  pip3 install --user {NRF_FLASHER}")


def birth_flash(connection: Connection, board: RNodeBoard, port: str,
                band_mhz: int = 915, version: str = FIRMWARE_VERSION,
                timeout: int = 400):
    """Flash + provision a board as an RNode, with the fresh-board second pass.

    A BRAND-NEW (never-flashed) ESP32 re-enumerates onto its RNode USB identity
    after the first firmware write, so a single ``--autoinstall`` typically
    flashes the firmware but cannot finish writing the EEPROM (identity, hash,
    signature) before the port changes underneath it. nodemedic makes the second
    pass part of the birth: run autoinstall once, and if the board was not
    already an RNode, run it a second time — now that firmware is present, the
    provisioning completes reliably. An already-provisioned board births in a
    single pass (no needless reflash).

    On a real local board (a connection exposing ``run_interactive``) the
    autoinstall is driven through a PTY, because rnodeconf's confirm/continue
    prompts read a keypress from the terminal — a plain ``printf | rnodeconf``
    pipe hangs there forever and wedges the USB port. The emulated/remote path
    keeps the pre-fed-stdin command.

    Returns ``(ok, message, already_provisioned)``.
    """
    # HARD GATE — autoinstall ERASES and reflashes; the medic's own radio must
    # never reach here (house rule; hole found by the 2026-08-01 bug hunt).
    try:
        from ui.onboard_roster import assert_flashable, guard_is_active
        if guard_is_active():
            assert_flashable(port)
    except Exception as e:            # noqa: BLE001
        return False, f"Refusing to flash: {e}", False
    try:
        interactions = autoinstall_interactions(board, band_mhz)   # validates band
    except ValueError as exc:
        return False, str(exc), False

    # nRF52 boards are flashed by rnodeconf shelling out to `adafruit-nrfutil
    # dfu serial`. When that binary is missing rnodeconf gets all the way to the
    # write, prints a pip hint and stops — after erasing nothing but having
    # spent the operator's time. Say so up front, and say how to fix it.
    if (board.platform or "").lower().startswith("nrf"):
        missing = _missing_nrf_flasher(connection)
        if missing:
            return False, missing, False

    if (board.platform or "").upper().startswith("ESP32"):
        port, _touch_note = _esp32_hw_cdc_touch(connection, port)

    if hasattr(connection, "run_interactive"):
        cmd = board.autoinstall_command(port, version=version, offline=True)
        code, out, _ = connection.run_interactive(cmd, interactions, timeout)
        if ALREADY_PROVISIONED_MARKER in out.lower():
            return True, "already a provisioned RNode", True
        if not _autoinstall_ok(code, out):
            # Second pass after the fresh-board re-enumeration finishes the EEPROM.
            code, out, _ = connection.run_interactive(cmd, interactions, timeout)
        ok = _autoinstall_ok(code, out)
        return (ok,
                "flashed + provisioned via autoinstall (PTY-driven)" if ok
                else f"autoinstall did not complete: {out[-200:]}",
                False)

    # Emulated / remote fallback: pre-feed the answers via stdin.
    cmd = flash_command(board, port, band_mhz, version)
    code, out, err = connection.run(cmd, timeout=timeout)
    if ALREADY_PROVISIONED_MARKER in out.lower():
        return True, "already a provisioned RNode", True
    code, out, err = connection.run(cmd, timeout=timeout)
    out_l = out.lower()
    ok = code == 0 and (SUCCESS_MARKER in out_l
                        or ALREADY_PROVISIONED_MARKER in out_l)
    return (ok,
            "flashed + provisioned over two passes (new board)" if ok
            else f"flash failed (exit {code}): {(err or out)[-200:]}",
            False)


#: The medic's built custom-fork image for the Heltec Wireless Tracker — the
#: SAME firmware its own Jonesey runs (patched markqvist fork + TFT motion
#: animation, TX-fix build of 2026-07-31). Flashing a work Tracker = copying
#: this proven image; native USB-JTAG needs --no-stub --baud 115200.
TRACKER_BUILD_DIR = "~/overlay_test/RNode_Firmware/build/esp32.esp32.esp32s3"
TRACKER_BOOT_APP0 = ("~/.arduino15/packages/esp32/hardware/esp32/2.0.17/"
                     "tools/partitions/boot_app0.bin")
TRACKER_ESPTOOL = "~/.arduino15/packages/esp32/tools/esptool_py/4.5.1/esptool.py"


def _identity_ok(low: str) -> bool:
    """Did the ROM-bootstrap leave the board with a valid identity? Written
    now, already validated — OR already there: a Tracker re-used from an
    earlier RNode build answers "a valid EEPROM was already present. No changes
    are being made", which is success, not failure (Node Medic 2's own radio
    set-up stopped on exactly this, 2026-10-06, and told the keeper to press
    RST for nothing)."""
    return "bootstrapping successful" in low or "signature validated" in low


def refused_before_write(message: str) -> bool:
    """Did birth_flash refuse BEFORE touching the board? Such a message means
    nothing was written and nothing foreign is in the way — so a chip erase
    is never the right follow-up."""
    m = (message or "").lstrip()
    return m.startswith(("Refusing to flash", "Node Medic can't flash",
                         "Node Medic hasn't verified", "Cannot pick",
                         "Autoinstall band"))


def cached_firmware_version(connection, preferred: str = "") -> str:
    """The firmware version the offline cache can actually flash, or "".

    *preferred* (the pin) wins when its directory holds release zips; else
    the version the last sync recorded in ``.rnm_bundle_version``; else the
    newest directory that holds zips. Pure shell listings, no network."""
    import re as _re

    def looks_like_version(v):
        # "1.86", "1.90.2" — never a stray word from a shell that answered
        # something else, and never a path component with teeth in it
        return bool(v) and _re.fullmatch(r"\d+(\.\d+)+", v) is not None

    def has_zips(v):
        return (looks_like_version(v)
                and connection.run(f"ls {RNODE_UPDATE_DIR}/{v}/*.zip")[0] == 0)
    if has_zips(preferred):
        return preferred
    code, out, _ = connection.run(f"cat {RNODE_UPDATE_DIR}/.rnm_bundle_version")
    synced = (out or "").strip().splitlines()[0].strip() if code == 0 and (out or "").strip() else ""
    if has_zips(synced):
        return synced
    code, out, _ = connection.run(f"ls {RNODE_UPDATE_DIR}")
    if code != 0:
        return ""
    vers = sorted((v.strip() for v in (out or "").splitlines()
                   if looks_like_version(v.strip())),
                  key=lambda v: [int(x) for x in v.split(".")], reverse=True)
    for v in vers:
        if has_zips(v):
            return v
    return ""


def fork_build_dir_for(board) -> str:
    """Where THIS board's fork image lives. The Tracker is flashed from the
    medic's own overlay build (TRACKER_BUILD_DIR); a board that names its own
    ``build_dir`` in the catalogue (the EoRa-S3) is flashed from that. Until
    2026-10-03 every custom board took the Tracker's directory — an EoRa-S3
    picked in BIRTH would have been written the Tracker's image (readiness
    sweep)."""
    return getattr(board, "build_dir", "") or TRACKER_BUILD_DIR


def fork_image_for(board, suffix: str) -> str:
    """Path of one artefact of this board's fork build: ``<build_dir>/<sketch>.<suffix>``
    — ``bin``, ``bootloader.bin``, ``partitions.bin``. The sketch name comes
    from the catalogue (a CE tree builds ``RNode_Firmware_CE.ino.*``); the
    upstream tree's ``RNode_Firmware.ino`` is the default."""
    base = getattr(board, "sketch", "") or "RNode_Firmware.ino"
    return f"{fork_build_dir_for(board)}/{base}.{suffix}"


def fork_flash_size_for(board) -> str:
    """esptool ``--flash_size`` for this board: from the catalogue FQBN
    (``FlashSize=4M`` -> ``4MB``), else the Tracker's proven ``8MB``. A full
    image (bootloader included) written at the wrong size boot-loops
    ([[v4-rgb-flash-size-detect]])."""
    m = re.search(r"FlashSize=(\d+)M\b", getattr(board, "fqbn", "") or "")
    return f"{m.group(1)}MB" if m else "8MB"


def usb_id_for_port(connection: Connection, port: str):
    """The /dev/serial/by-id basename for *port* — the board's USB fingerprint
    (contains the chip MAC for native-CDC ESP32-S3s, the adapter serial for
    CP2102 bridges like the V3's). A plain RNode has NO Reticulum identity,
    so this fingerprint is how the medic recognises one of its own later
    (operator report 2026-08-01: a just-flashed RNode wasn't offered as kin)."""
    out = connection.run(
        f'for l in /dev/serial/by-id/*; do t=$(readlink -f "$l" 2>/dev/null); '
        f'[ "$t" = "{port}" ] && basename "$l"; done 2>/dev/null')[1]
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return lines[0] if lines else None


# The serial reader is shared, in provisioning.by_id (imported at the top of
# this module). It used to be a local regex anchored with ``-if\d+$``, which
# silently returned nothing for USB-UART BRIDGE boards (CP2102/FTDI: Heltec V3,
# T-Beam) because their ``-if00-port0`` tail pushed the ``$`` past the match —
# the very re-enumeration this reader exists to survive. See provisioning/by_id.


def find_port_by_usb_serial(connection, serial, tries: int = 20,
                            delay: float = 1.0, sleep=None):
    """Poll for the tty currently carrying USB *serial*, or None.

    Needed because a board does not keep its port number across a flash. An
    nRF52 in DFU (239a:002a) and the same board running RNode firmware
    (239a:8029) are two different USB devices to the kernel, so the tty is
    re-issued — verified live 2026-08-05: a RAK4631 flashed on ``ttyACM1`` came
    back on ``ttyACM2``. Polling rather than a fixed sleep because the gap
    between the write finishing and udev publishing the new symlink is not
    fixed.
    """
    if not serial:
        return None
    import time as _t
    nap = sleep or _t.sleep
    for attempt in range(max(1, tries)):
        out = connection.run(
            'for l in /dev/serial/by-id/*; do echo "$l $(readlink -f "$l")"; '
            'done 2>/dev/null')[1] or ""
        for line in out.splitlines():
            parts = line.split()
            if len(parts) == 2 and by_id_serial(os.path.basename(parts[0])) == serial:
                return parts[1]
        if attempt < tries - 1:
            nap(delay)
    return None


#: The Adafruit nRF52 bootloader's USB product id. In DFU the board enumerates
#: as this instead of its application id (0x8029 on the RAK4631).
#: Every nRF52 UF2-bootloader PID we may meet, from Columba's device-verified
#: table (NordicDFUFlasher.kt) cross-checked against our own bench: 0x0029
#: (RAK-customized — though our T-Echo has ALSO presented it), 0x002a (generic
#: pca10056 — T-Echo usually), 0x0071 (Heltec T114 HT-n5262). Knowing one PID
#: mis-read a RAK or T114 sitting in DFU as "running".
NRF_DFU_PIDS = ("0029", "002a", "0071")
NRF_DFU_PID = "002a"          # kept for existing callers/tests


def in_dfu_already(connection, port: str, vendor_fn=None, pid_fn=None) -> bool:
    """Is this nRF52 board already sitting in its bootloader?

    Cheap check so a board that is ALREADY in DFU is not reset back out of it.
    """
    try:
        from ui.board_detect import _NRF52_VENDORS
        if vendor_fn is None:
            from ui.board_detect import port_usb_vendor as vendor_fn
        if (vendor_fn(port) or "").lower() not in _NRF52_VENDORS:
            return False
        if pid_fn is None:
            from ui.board_detect import port_usb_product_id as pid_fn
        return (pid_fn(port) or "").lower() in NRF_DFU_PIDS
    except Exception:                                             # noqa: BLE001
        return False


def touch_into_dfu(connection, port: str, serial: str, settle: float = 2.0,
                   sleep=None):
    """Reset an nRF52 board into its bootloader, then return its NEW port.

    Why this exists. rnodeconf is handed one port and expects it to stay put,
    but opening the port of a RUNNING RNode toggles DTR and resets the board:
    it re-enumerates as the bootloader on a DIFFERENT tty, and rnodeconf then
    dies with "Could not find specified port /dev/ttyACM2, exiting now" (live,
    2026-08-05, birthing an already-provisioned RAK4631). Re-acquiring the port
    BEFORE the flash cannot help — the port was still valid when we looked; it
    vanishes underneath rnodeconf a moment later.

    So do the move ourselves and let it settle first. Once the board is in DFU,
    autoinstall's own 1200-baud touch is a no-op and the port it was given stays
    valid for the whole run — which is exactly why birthing a board that arrived
    ALREADY in DFU worked while an already-provisioned one did not.

    The touch is the standard Arduino-core convention: open at 1200 baud, drop
    DTR, close. Verified live on a RAK4631 (239a:8029 -> 239a:002a).

    Returns the port the board is on afterwards — possibly unchanged.
    """
    import time as _t
    nap = sleep or _t.sleep
    quoted = shlex.quote(port)
    connection.run(
        "python3 -c \"import serial,time; "
        f"s=serial.Serial({quoted!r}, 1200); s.dtr=False; "
        "time.sleep(0.25); s.close()\" 2>/dev/null || true", timeout=30)
    nap(settle)
    # Forward the injected sleep: find_port_by_usb_serial polls with its own
    # naps, and without this a test that hands in a no-op sleep still waits the
    # real ~19s of udev polling.
    found = find_port_by_usb_serial(connection, serial, sleep=nap)
    return found or port


def wipe_for_rebirth(connection, port: str, esptool_path: str = "",
                     vendor_fn=None, timeout: int = 120, sleep=None,
                     tries: int = 4):
    """Make a birthed board blank again, by whatever its CHIP FAMILY supports.

    A rebirth used to run ``esptool erase_flash`` unconditionally. On a RAK4631
    that produced "Rebirth failed — Couldn't wipe the board: Could not connect
    to Espressif device: No serial data received" (live, 2026-08-05), because
    esptool cannot talk to an nRF52 at all. The board was fine; the tool was
    simply the wrong one, and the error pointed at Espressif's troubleshooting
    page for a chip that isn't on the board.

    * **nRF52** — there is no chip erase to run. The Adafruit bootloader is the
      only way back in, so erasing it would brick the board rather than reset
      it. What a rebirth actually needs is for the board to stop presenting as a
      provisioned RNode, and ``rnodeconf --eeprom-wipe`` does exactly that. The
      firmware itself is fully overwritten by the next DFU flash regardless.
    * **ESP32** — full chip erase, as before, so a board carrying foreign
      firmware becomes the blank slate autoinstall expects.

    Retries through the "could not exclusively lock port" race, where the detect
    step's banner read still holds the port for a beat.

    Returns ``(ok, message)``.
    """
    from ui.onboard_roster import assert_flashable, guard_is_active
    if guard_is_active():
        assert_flashable(port)               # NEVER the medic's own radio

    if vendor_fn is None:
        from ui.board_detect import port_usb_vendor as vendor_fn
    from ui.board_detect import _NRF52_VENDORS
    is_nrf = (vendor_fn(port) or "").lower() in _NRF52_VENDORS

    if is_nrf:
        cmd = f"rnodeconf {shlex.quote(port)} --eeprom-wipe"
    elif esptool_path:
        cmd = f"python3 {shlex.quote(esptool_path)} --port {shlex.quote(port)} erase_flash"
    else:
        return False, "Couldn't find the board or the flash tool."

    import time as _t
    nap = sleep or _t.sleep
    msg = ""
    for attempt in range(max(1, tries)):
        code, out, err = connection.run(cmd, timeout=timeout)
        low = ((out or "") + (err or "")).lower()
        if code == 0 or "erase completed" in low or "eeprom wiped" in low:
            return True, "wiped"
        msg = ((err or "") or (out or "")).strip()[-160:]
        if attempt < tries - 1:
            nap(3)
    return False, msg


class RNodeFlashWorkflow:
    def __init__(self, connection: Connection, board: RNodeBoard,
                 port: Optional[str] = None, band_mhz: int = 915,
                 version: str = FIRMWARE_VERSION, flash_timeout: int = 400,
                 radio=None, work_ports_fn=None):
        self.connection = connection
        self.board = board
        self.port = port
        self.band_mhz = band_mhz
        self.version = version
        self.flash_timeout = flash_timeout
        # Radio params baked at birth. Default = the SAVED tool-wide defaults
        # (Settings ▸ Default radio parameters), so a regional operator's
        # settings reach every flash; the BIRTH form can still override.
        if radio is None:
            try:
                from provisioning.radio_defaults import load_radio_config
                radio = load_radio_config()
            except Exception:
                radio = None                 # set_params falls back to canonical
        self.radio = radio
        # WORK-board enumerator (the medic's own radios excluded) — without
        # it the single-board check counts Jonesey/the GPS tracker and can
        # NEVER pass on the real medic (first live V3 lap, 2026-08-01).
        self.work_ports_fn = work_ports_fn
        self.results: List[StepResult] = []

    # -- steps -------------------------------------------------------------

    def _say(self, words: str) -> None:
        """Sub-step words for the screen (a flash is one step, three acts):
        the keeper's rule — every running process shows movement."""
        cb = getattr(self, "say", None)
        if cb:
            try:
                cb(words)
            except Exception:                                  # noqa: BLE001
                pass

    def _detect_port(self) -> StepResult:
        why = self.board.cannot_flash_reason(self.band_mhz)
        if not why:
            # The same gate the pickers use: a custom-fork / DFU board whose
            # build this medic does not carry is refused HERE, before a port
            # is opened, so the gate holds for every caller of the workflow
            # and not only for the two pickers (readiness ledger #168, #43).
            try:
                from ui.usb_ports import connection_is_local
                if connection_is_local(self.connection):
                    from ui.birth import board_blocker
                    why = board_blocker(self.board, self.band_mhz)
            except Exception:                                      # noqa: BLE001
                why = ""
        if why:
            # Said FIRST, before any port is opened: this used to come out
            # after three green steps and a chip erase (2026-10-03).
            return StepResult("detect_port", False, why)
        port = self.port or detect_rnode_port(self.connection)
        if not port:
            return StepResult("detect_port", False,
                              "No board found — plug it in (some USB-C cables "
                              "are charge-only).")
        self.port = port
        # Fingerprint the board the moment we first see it. The tty number is
        # the one property that does NOT survive a reset, and every later step
        # needs a way back to THIS board rather than to whatever now holds the
        # number it used to have.
        self._usb_serial = by_id_serial(usb_id_for_port(self.connection, port))
        # "Board on Port 3 (/dev/ttyACM1)." where the engraved-hole map knows
        # the hole; the raw path (unchanged behaviour) everywhere else — and
        # a port an emulated/remote connection reported is never labelled.
        from ui.usb_ports import connection_is_local, describe_port
        return StepResult(
            "detect_port", True,
            f"Board on {describe_port(port, local=connection_is_local(self.connection))}.")

    def _ensure_single_board(self) -> StepResult:
        # Flashing erases/re-provisions the EEPROM; never guess between boards.
        # Count WORK boards only — the medic's own permanent radios (Jonesey,
        # the GPS tracker) are always plugged in and must not trip this.
        if self.work_ports_fn is not None:
            ports = list(self.work_ports_fn())
        else:
            out = self.connection.run("ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")[1]
            ports = [p for p in out.split() if p.startswith("/dev/")]
        if len(ports) > 1:
            return StepResult(
                "ensure_single_board", False,
                f"{len(ports)} work boards connected ({', '.join(ports)}). "
                f"Unplug all but the one you want to flash.")
        if not ports:
            return StepResult(
                "ensure_single_board", False,
                "The board vanished from USB — check the cable and replug.")
        # The count passing means nothing if we're aimed somewhere else: the
        # pinned port must BE that one work board (2026-08-01 bug hunt).
        if self.port and self.work_ports_fn is not None and self.port not in ports:
            return StepResult(
                "ensure_single_board", False,
                f"The board moved: this build targets {self.port}, but the "
                f"attached work board is {ports[0]}. Replug it and start again.")
        return StepResult("ensure_single_board", True, "One work board connected.")

    def _ensure_firmware(self) -> StepResult:
        if self.board.flash_method == "serial_dfu":
            # The board names its own build, because a second hardcoded path is
            # how the wrong board's image gets flashed. Checked BEFORE the
            # board is touched: discovering the artefact is missing after a
            # 1200-baud touch would leave it sitting in DFU for nothing.
            pkg = f"{self.board.build_dir}/{self.board.dfu_package}"
            if self.connection.run(f"test -f {pkg}")[0] != 0:
                return StepResult(
                    "ensure_firmware", False,
                    f"This Node Medic has no {self.board.display_name} firmware "
                    f"on it ({pkg}). That board's firmware is built from source "
                    f"and this medic doesn't carry the build — choose a board "
                    f"the medic has firmware for, or ask for a release that "
                    f"carries this one.")
            return StepResult("ensure_firmware", True,
                              f"{self.board.display_name} firmware ready "
                              "(the medic's own build).")
        if self.board.flash_method != "autoinstall":
            # custom fork: THIS board's build is the firmware source
            d = fork_build_dir_for(self.board)
            if self.connection.run(f"test -f {fork_image_for(self.board, 'bin')}")[0] != 0:
                return StepResult(
                    "ensure_firmware", False,
                    f"This Node Medic has no {self.board.display_name} firmware "
                    f"on it ({d}). That board's firmware is built from source "
                    f"and this medic doesn't carry the build — choose a board "
                    f"the medic has firmware for, or ask for a release that "
                    f"carries this one.")
            return StepResult("ensure_firmware", True,
                              f"{self.board.display_name} firmware ready "
                              "(the medic's own build).")
        if has_connectivity(self.connection):
            res = sync_firmware(self.connection)
            if res.failed:
                # Being online must never make a flash LESS reliable than
                # being offline: a failed download falls back to whatever the
                # cache already holds (readiness ledger #50).
                cached = cached_firmware_version(self.connection, self.version)
                if cached:
                    self.version = cached
                    return StepResult("ensure_firmware", True,
                                      f"Online sync failed for "
                                      f"{', '.join(res.failed[:3])} — flashing the "
                                      f"carried {cached} instead.")
                return StepResult("ensure_firmware", False,
                                  f"Firmware sync failed for "
                                  f"{', '.join(res.failed[:3])} and nothing is "
                                  f"cached for this board yet — try again with a "
                                  f"better connection.")
            if getattr(res, "version", ""):
                self.version = res.version          # flash what was fetched
            return StepResult("ensure_firmware", True,
                              f"Firmware ready ({res.message}).")
        # Offline: the carried cache must hold SOME complete version. The
        # online sync fetches whatever release is current, so the cache can
        # hold 1.87 while the pin says 1.86 — and this step used to look for
        # the pin alone and refuse with "connect WiFi" on a medic that had
        # just been online (readiness sweep, 2026-10-03).
        cached = cached_firmware_version(self.connection, self.version)
        if not cached:
            return StepResult("ensure_firmware", False,
                              f"Offline and no RNode firmware is cached on this "
                              f"medic. Connect WiFi once to seed the cache.")
        self.version = cached
        return StepResult("ensure_firmware", True,
                          f"Offline — using cached firmware {cached}.")

    def _reacquire_port(self) -> str:
        """Re-find the board after a flash, because the tty can MOVE.

        Live 2026-08-05: a RAK4631 was flashed on ``ttyACM1`` and came back on
        ``ttyACM2`` — DFU and the running firmware are different USB devices, so
        the kernel issues a new tty. ``set_params`` then aimed at a node that no
        longer existed, ``serial_for_port`` returned None, and the onboard guard
        (rightly) refused to write to something it could not identify. The guard
        was correct: writing blind to a vanished tty is exactly how the wrong
        board gets flashed once something else claims that number.

        Only re-point when the CURRENT port has genuinely gone; a board that
        stayed put is left alone.
        """
        serial = getattr(self, "_usb_serial", None)
        if not serial:
            return self.port
        if self.connection.run(f"test -e {self.port}")[0] == 0:
            return self.port                          # still there, nothing to do
        found = find_port_by_usb_serial(self.connection, serial)
        if found and found != self.port:
            self.port = found
        return self.port

    def _flash(self) -> StepResult:
        # EVERY road signs the board's EEPROM with this medic's own key; a
        # fresh clone has none and rnodeconf stops at "No signing key found"
        # (Node Medic 2, 2026-10-06). Make it first, once.
        from workflows.signing_key import ensure_signing_key
        if not (hasattr(self.connection, "run") and self.connection.run(
                "test -f ~/.config/rnodeconf/firmware/signing.key")[0] == 0):
            self._say("Making this medic's own signing key — first time only")
        k_ok, k_note = ensure_signing_key(self.connection)
        if not k_ok:
            return StepResult("flash", False, k_note)
        if self.board.flash_method == "serial_dfu":
            return self._flash_serial_dfu()
        if self.board.flash_method != "autoinstall":
            return self._flash_custom_fork()
        # The board can move between detect_port and here — an already-birthed
        # RNode re-enumerates whenever anything opens its port, so by the time
        # autoinstall ran it was on a different tty and rnodeconf died with
        # "Could not find specified port /dev/ttyACM2, exiting now" (live,
        # 2026-08-05, lap 2 of the acceptance matrix). Re-point before the write
        # as well as after it.
        if not getattr(self, "_usb_serial", None):
            self._usb_serial = by_id_serial(
                usb_id_for_port(self.connection, self.port))
        self._reacquire_port()
        # An nRF52 that is RUNNING resets the instant its port is opened, so it
        # would re-enumerate onto a new tty in the middle of autoinstall and
        # rnodeconf would lose the port it was given. Make the move happen HERE,
        # where we can wait for it and follow the board, instead of underneath a
        # tool that cannot. A board already in DFU is left alone.
        if ((self.board.platform or "").lower().startswith("nrf")
                and self._usb_serial
                and not in_dfu_already(self.connection, self.port)):
            self.port = touch_into_dfu(self.connection, self.port,
                                       self._usb_serial)
        ok, msg, already = birth_flash(self.connection, self.board, self.port,
                                       self.band_mhz, self.version,
                                       self.flash_timeout)
        if not ok and not already and refused_before_write(msg):
            # birth_flash refused before touching the board (the onboard guard,
            # an unanswerable band): there is no foreign firmware to blame and
            # nothing to erase. Erasing here wiped a newcomer's working T-Beam
            # and then told them to use a terminal (readiness sweep, 2026-10-03).
            return StepResult("flash", False, msg)
        if not ok and not already:
            # A board that arrives carrying FOREIGN firmware (factory image,
            # Meshtastic, a half-written flash) can present a USB serial port
            # while speaking nothing rnodeconf understands, and autoinstall
            # then dies at its post-write probe — "Serial port opened, but
            # RNode did not respond" (live: a fresh T-Beam Supreme,
            # 2026-08-01). The RTNode path never hits this because it ERASES
            # first. Do the same here, once, then retry: on a truly blank chip
            # autoinstall has nothing to be confused by.
            if self.connection.run(f"test -e {self.port}")[0] != 0:
                # The board left the bus mid-flash (cable, brown-out). Erasing
                # "it" now trips the onboard guard and blames the medic's own
                # radio (readiness ledger #169) — say what happened instead.
                return StepResult("flash", False,
                                  "The board left USB during the flash — check the "
                                  "cable, plug it back in, then try again.")
            erased, emsg = self._erase_chip()
            if erased:
                ok, msg, already = birth_flash(
                    self.connection, self.board, self.port, self.band_mhz,
                    self.version, self.flash_timeout)
                msg = f"{msg} (after erasing the board's foreign firmware)"
            else:
                msg = f"{msg} — and the board could not be erased: {emsg}"
        if already:
            # Re-inserted an already-flashed board — birthing is already done.
            return StepResult(
                "flash", True,
                f"{self.board.display_name} is already a provisioned RNode — "
                f"no flash needed (wipe the EEPROM first to force a reflash).",
                skipped=True)
        return StepResult(
            "flash", ok,
            f"Flashed {self.board.display_name} from the offline cache — {msg}."
            if ok else f"Flash failed: {msg}")

    def _erase_chip(self):
        """Full chip erase via the bundled esptool, so a board carrying
        foreign firmware becomes the blank slate autoinstall expects. Gated
        like every other write boundary. Returns (ok, message)."""
        try:
            from ui.onboard_roster import assert_flashable, guard_is_active
            if guard_is_active():
                assert_flashable(self.port)
        except Exception as e:            # noqa: BLE001
            return False, f"refused: {e}"
        # Clear the board by the means ITS CHIP FAMILY has. This fallback exists
        # for a board arriving with foreign firmware — a factory image being the
        # commonest case — so running esptool unconditionally aimed the wrong
        # tool at exactly the boards it was written to rescue. An nRF52 got
        # "could not be erased: Could not connect to Espressif device", which
        # says nothing true about a board that has no Espressif chip on it.
        try:
            from ui.board_detect import _NRF52_VENDORS, port_usb_vendor
            is_nrf = (port_usb_vendor(self.port) or "").lower() in _NRF52_VENDORS
        except Exception:                                         # noqa: BLE001
            is_nrf = False
        if is_nrf:
            # No chip erase exists here: the Adafruit bootloader is the only way
            # back in, and erasing it bricks rather than resets. What actually
            # confuses rnodeconf's post-write probe is a stale/invalid EEPROM,
            # and the DFU write replaces the application wholesale regardless.
            cmd = f"rnodeconf {shlex.quote(self.port)} --eeprom-wipe"
        else:
            cmd = (f"python3 ~/.config/rnodeconf/update/{self.version}/esptool.py "
                   f"--chip auto --port {self.port} --before default_reset "
                   f"erase_flash")
        code, out, err = self.connection.run(cmd, timeout=self.flash_timeout)
        low = ((out or "") + (err or "")).lower()
        ok = code == 0 or "erase completed" in low or "eeprom wiped" in low
        return ok, ("erased" if ok else (err or out or "")[-160:])

    def _flash_custom_fork(self) -> StepResult:
        """Flash a custom-fork board (the Wireless Tracker) from the medic's
        own PROVEN build — the image Jonesey runs — then ROM-bootstrap the
        EEPROM with the board's provision codes and set the firmware hash so
        it validates. One step, three acts, because the fixed step list
        predates custom boards."""
        try:                              # NEVER the medic's own radio
            from ui.onboard_roster import assert_flashable, guard_is_active
            if guard_is_active():         # strict on the medic; dev hosts
                assert_flashable(self.port)   # have no radio to protect
        except Exception as e:            # noqa: BLE001
            return StepResult("flash", False, f"Refusing to flash: {e}")
        d = fork_build_dir_for(self.board)
        size = fork_flash_size_for(self.board)
        # Capture the board's USB fingerprint BEFORE the flash: the S3 hard-
        # resets afterwards, its CDC identity vanishes and returns (sometimes
        # on a NEW ttyACM number), and the old "sleep 4 then talk to the old
        # path" raced that re-enumeration — "Serial port opened, but RNode
        # did not respond" on the operator's screen, 2026-08-30. Same lesson
        # the nRF family learned on 2026-08-05; this path never got it.
        pre_byid = usb_id_for_port(self.connection, self.port)
        pre_serial = by_id_serial(pre_byid) if pre_byid else None
        # esptool's own reset into download mode before the write
        # (--before default_reset): the S3's USB goes quiet unless it is
        # freshly reset for each command (Node Medic 2's Tracker, 2026-10-06).
        # No erase_flash here: the S3 ROM has no erase without the stub, and
        # native USB-JTAG is driven --no-stub. The old identity is wiped by
        # the booted firmware instead (--eeprom-wipe, below), so a board
        # re-used from an earlier build is provisioned fresh under THIS medic.
        self._say("Writing the radio software (two to three minutes)…")
        # IN PIECES, WITH RETRIES. A Tracker's power dips under a sustained
        # write: the board dropped off USB at the first big flash region on
        # three cables and two sockets, with and without the stub (Node Medic
        # 2, 2026-10-06). 64 KB pieces, each erased and written in a short
        # burst, each retried after the board comes back, landed the whole
        # image first time. The small parts go first in one call.
        head = self.connection.run(
            f"python3 {TRACKER_ESPTOOL} --chip esp32s3 --port {self.port} "
            f"--baud 115200 --no-stub --before default_reset --after no_reset "
            f"write_flash -z --flash_size detect "
            f"0x0 {fork_image_for(self.board, 'bootloader.bin')} "
            f"0x8000 {fork_image_for(self.board, 'partitions.bin')} "
            f"0xe000 {TRACKER_BOOT_APP0}",
            timeout=self.flash_timeout)
        code, out, err = head
        low = ((out or "") + (err or "")).lower()
        if code == 0 and "hash of data verified" in low:
            code, out, err = self._write_app_in_pieces(fork_image_for(self.board, "bin"),
                                                       pre_serial)
        low = ((out or "") + (err or "")).lower()
        if "input/output error" in low or "could not configure port" in low:
            # the board vanished from USB MID-WRITE: a half-written image
            # boot-loops with a dark screen. Say what happened, not "failed".
            from workflows.own_supply import with_supply_note
            return StepResult("flash", False, with_supply_note(
                              "The board dropped off USB part-way through the "
                              "write, so its radio software is incomplete. Check the "
                              "cable, or try another cable or another USB "
                              "socket, then press Try again — Node Medic "
                              "rewrites it from the start.", self.connection.run))
        if code != 0 and "hash of data verified" not in low:
            return StepResult("flash", False,
                              f"esptool write failed: {(err or out)[-200:]}")
        self._say("Giving the Tracker its name…")
        # SETTLE: wait for the SAME board (by USB serial) to re-appear and
        # re-resolve its port — never trust the pre-flash path. Then give the
        # fresh app a breath before speaking KISS to it.
        settled = find_port_by_usb_serial(self.connection, pre_serial,
                                          tries=25, delay=1.0)
        if settled:
            self.port = settled
        if not self._wait_for_rnode(self.port):
            # still in download mode (or slow): one more proper reset, one more wait
            self._reset_into_app(self.port)
            self._wait_for_rnode(self.port)
        # boot, then ROM-bootstrap as the custom product (cb/ca for the
        # Tracker) — signed with the medic's project key. One retry after a
        # fresh settle: the first attempt can still land inside the app's
        # own boot window.
        # a board re-used from an earlier build still carries that identity;
        # "-r" then changes nothing. Wipe it first so this medic's own
        # identity goes on (best-effort: a blank board answers "no EEPROM").
        wcode, wout, werr = self.connection.run(
            f"sleep 4 && rnodeconf {self.port} --eeprom-wipe", timeout=90)
        # the tool's own words go to ui.log (never the screen): the Node Medic 2
        # naming failure of 2026-10-06 was undiagnosable without them
        print("[flash] eeprom-wipe:", wcode, " ".join(((wout or "") + (werr or "")).split())[-300:],
              flush=True)
        p = self.board.provision or {}
        boot_cmd = (f"sleep 4 && rnodeconf {self.port} -r "
                    f"--product {p.get('product', 'cb')} "
                    f"--model {p.get('model', 'ca')} "
                    f"--platform {p.get('platform', '0x80')} "
                    f"--hwrev {p.get('hwrev', '1')}")
        code, out, err = self.connection.run(boot_cmd,
                                             timeout=self.flash_timeout)
        low = ((out or "") + (err or "")).lower()
        if not _identity_ok(low):
            settled = find_port_by_usb_serial(self.connection, pre_serial,
                                              tries=15, delay=1.0)
            if settled:
                self.port = settled
                boot_cmd = (f"sleep 4 && rnodeconf {self.port} -r "
                            f"--product {p.get('product', 'cb')} "
                            f"--model {p.get('model', 'ca')} "
                            f"--platform {p.get('platform', '0x80')} "
                            f"--hwrev {p.get('hwrev', '1')}")
            code, out, err = self.connection.run(boot_cmd,
                                                 timeout=self.flash_timeout)
            low = ((out or "") + (err or "")).lower()
        print("[flash] bootstrap:", code, " ".join(low.split())[-300:], flush=True)
        if not _identity_ok(low):
            # NO LIES THROUGH THE UI (operator, 2026-08-30): the firmware IS
            # on the board — only its papers failed. Reporting this as a
            # failed "flash" sent the operator into the won't-flash recovery
            # ritual (hold PRG, press RST) for a problem that isn't flashing,
            # three laps in a row. Say what actually happened, and give the
            # advice that fits THIS act.
            if "already present" in low:
                return StepResult("flash", False,
                                  "The radio software is on the Tracker, but it "
                                  "kept its old name and Node Medic could not "
                                  "replace it. Unplug the Tracker, plug it back "
                                  "in, then press Try again.")
            return StepResult("flash", False,
                              "The radio software is on the Tracker; giving it "
                              "its name didn't finish. Press Try again — Node "
                              "Medic resets the Tracker itself.")
        # firmware hash = the app image's embedded SHA (validates, not corrupt)
        from workflows.rnode_v4_rgb import embedded_hash_command
        self.connection.run(
            embedded_hash_command(self.port, fork_image_for(self.board, "bin")),
            timeout=120)
        return StepResult(
            "flash", True,
            f"Flashed the medic's proven Tracker fork image and provisioned "
            f"as {self.board.display_name}.")

    def _write_app_in_pieces(self, image: str, serial, piece: int = 64 * 1024,
                             tries: int = 3):
        """Write *image* at 0x10000 in *piece*-sized parts, each with esptool's
        own reset into download mode and up to *tries* goes, re-resolving the
        port by USB serial when the board re-enumerates. Returns the usual
        (code, out, err) with the LAST esptool output, code 0 on success."""
        import os as _os
        total = 0
        try:                      # the medic flashes its own USB: the image is local
            total = _os.path.getsize(_os.path.expanduser(image))
        except OSError:
            size_out = self.connection.run(f"stat -c %s {image}")[1]
            try:
                total = int((size_out or "0").strip())
            except ValueError:
                total = 0
        if total <= 0:
            # size unknown (a remote or emulated target): one whole write
            return self.connection.run(
                f"python3 {TRACKER_ESPTOOL} --chip esp32s3 --port {self.port} "
                f"--baud 115200 --no-stub --before default_reset --after no_reset "
                f"write_flash --flash_size detect 0x10000 {image}",
                timeout=self.flash_timeout)
        n_pieces = (total + piece - 1) // piece
        last = (1, "", "")
        for i in range(n_pieces):
            off = 0x10000 + i * piece
            self._say(f"Writing the radio software… part {i + 1} of {n_pieces}")
            part = f"/tmp/nm-part-{i}.bin"
            self.connection.run(f"dd if={image} of={part} bs={piece} skip={i} count=1 "
                                f"status=none", timeout=30)
            ok = False
            for attempt in range(tries):
                if attempt:
                    found = find_port_by_usb_serial(self.connection, serial, tries=15,
                                                    delay=1.0)
                    if found:
                        self.port = found
                last = self.connection.run(
                    f"python3 {TRACKER_ESPTOOL} --chip esp32s3 --port {self.port} "
                    f"--baud 115200 --no-stub --before default_reset --after no_reset "
                    f"write_flash --flash_size detect {hex(off)} {part}",
                    timeout=180)
                low = ((last[1] or "") + (last[2] or "")).lower()
                if last[0] == 0 and "hash of data verified" in low:
                    ok = True
                    break
            self.connection.run(f"rm -f {part}")
            if not ok:
                return last
        # all parts in: leave download mode and boot the app. NOT esptool's
        # "--after hard_reset": on a native-USB ESP32-S3 (USB-Serial/JTAG) it
        # pulses RTS with DTR still asserted, which the chip ignores — the
        # Tracker sat in download mode, dark, "not responding", through every
        # attempt of 2026-10-06. Drop DTR first, then pulse RTS.
        self._reset_into_app(self.port)
        return 0, "Hash of data verified. (written in pieces)", ""

    def _wait_for_rnode(self, port: str, tries: int = 6) -> bool:
        """Poll until the board speaks RNode (KISS) — ``rnodeconf --info``
        answers — so the naming step never talks to a board still booting or
        still in download mode. ~5 s per try."""
        for _ in range(tries):
            code, out, err = self.connection.run(
                f"sleep 3 && rnodeconf {port} --info", timeout=60)
            low = ((out or "") + (err or "")).lower()
            if "firmware version" in low or "device signature" in low or "not provisioned" in low \
                    or "no eeprom" in low or "eeprom bootstrap" in low:
                return True
        print("[flash] board never answered KISS after flashing", flush=True)
        return False

    def _reset_into_app(self, port: str) -> bool:
        """Reset a native-USB ESP32-S3 out of download mode into its program:
        DTR low, then an RTS pulse (the only sequence the USB-Serial/JTAG
        peripheral treats as a plain reset). Returns True when the port
        answered to the pulse."""
        code, out, err = self.connection.run(
            "python3 -c \"import serial,time\n"
            "s=serial.Serial(); s.port=%r; s.baudrate=115200; s.dtr=False; s.rts=False; s.open()\n"
            "s.dtr=False; s.rts=True; time.sleep(0.15); s.rts=False; s.close(); print('pulsed')\""
            % port, timeout=30)
        print("[flash] reset into app:", code, " ".join(((out or "") + (err or "")).split())[-120:],
              flush=True)
        return code == 0 and "pulsed" in (out or "")

    def _flash_serial_dfu(self) -> StepResult:
        """Flash an nRF52 board over its serial DFU bootloader.

        The whole sequence is the one proven by hand on a MeshPocket
        2026-09-01, in that order and for these reasons:

        1. Guard, then capture the USB serial. EVERY later step re-resolves the
           port from that serial and never reuses a path, because this board
           moves: after a medic reboot it took ttyACM0, which had been Jonesey.
        2. 1200-baud touch to enter DFU. No button to press.
        3. adafruit-nrfutil, then grep the OUTPUT for "Device programmed." —
           it exits 0 even when the flash failed, so the exit code is not
           evidence.
        4. Provision, then set the firmware hash. Skipping the hash leaves the
           board showing FIRMWARE CORRUPT on its own screen while rnodeconf
           still says "signature validated" — those are two different things.
        """
        try:                              # NEVER the medic's own radio
            from ui.onboard_roster import assert_flashable, guard_is_active
            if guard_is_active():
                assert_flashable(self.port)
        except Exception as e:            # noqa: BLE001
            return StepResult("flash", False, f"Refusing to flash: {e}")

        pkg = f"{self.board.build_dir}/{self.board.dfu_package}"
        pre_serial = by_id_serial(usb_id_for_port(self.connection, self.port))
        if not pre_serial:
            return StepResult(
                "flash", False,
                "Could not read this board's USB serial number. That number is "
                "the only thing telling it apart from the medic's own radio "
                "once it reboots into its bootloader, so the flash stops here "
                "rather than write to a port that may have moved.")

        # 1200-baud touch. The board resets the instant the port opens, so the
        # host often sees the write fail — that is the touch working, not a
        # fault, and only the DFU device appearing decides it.
        self.connection.run(
            f"python3 -c \"import serial,time; "
            f"s=serial.Serial('{self.port}',1200); s.dtr=False; "
            f"time.sleep(0.4); s.close()\" 2>/dev/null || true", timeout=30)

        dfu_port = find_port_by_usb_serial(self.connection, pre_serial,
                                           tries=30, delay=1.0)
        if not dfu_port:
            return StepResult(
                "flash", False,
                "The board never came back in its bootloader after the reset. "
                "Press RST twice quickly and run it again — nothing has been "
                "written, so the board is unchanged.")
        self.port = dfu_port
        try:
            from ui.onboard_roster import assert_flashable, guard_is_active
            if guard_is_active():         # re-check: the port MOVED
                assert_flashable(self.port)
        except Exception as e:            # noqa: BLE001
            return StepResult("flash", False, f"Refusing to flash: {e}")

        code, out, err = self.connection.run(
            f"adafruit-nrfutil dfu serial -pkg {pkg} -p {self.port} "
            f"-b 115200 --singlebank", timeout=self.flash_timeout)
        if "device programmed." not in ((out or "") + (err or "")).lower():
            return StepResult(
                "flash", False,
                f"Firmware write failed: {((err or out) or '')[-200:]}. Serial "
                "DFU erases before it writes, so the board is sitting in its "
                "bootloader and can simply be flashed again.")

        settled = find_port_by_usb_serial(self.connection, pre_serial,
                                          tries=30, delay=1.0)
        if settled:
            self.port = settled
        for cmd in self.board.provision_commands(self.port):
            self.connection.run(f"sleep 3 && {cmd}", timeout=self.flash_timeout)

        # The firmware hash is what clears FIRMWARE CORRUPT. Ask the DEVICE what
        # it computed rather than hashing the file here: if the two disagree the
        # right answer is to leave it unset and say so, not to paper over it.
        code, out, err = self.connection.run(
            f"rnodeconf {self.port} -L", timeout=120)
        import re as _re
        m = _re.search(r"\b([0-9a-f]{64})\b", (out or "") + (err or ""))
        if not m:
            return StepResult(
                "flash", True,
                f"Flashed and provisioned as {self.board.display_name}, but the "
                "board did not report its firmware hash, so it will show "
                "FIRMWARE CORRUPT until that is set. The firmware itself is on "
                "the board and sound.")
        self.connection.run(
            f"rnodeconf {self.port} --firmware-hash {m.group(1)}", timeout=120)
        return StepResult(
            "flash", True,
            f"Flashed the medic's own {self.board.display_name} build over "
            "serial DFU, provisioned it, and set its firmware hash.")

    def _set_params(self) -> StepResult:
        # Bake the canonical radio params into the EEPROM AT BIRTH and leave the
        # board host-controlled, so a Pi's rnsd never aborts on a stale
        # 250/SF11 default ("Radio state mismatch").
        self._reacquire_port()          # the flash may have moved the board
        ok, detail = set_params_at_birth(self.connection, self.port,
                                         cfg=self.radio,
                                         timeout=self.flash_timeout)
        return StepResult("set_params", ok, detail)

    def _verify(self) -> StepResult:
        self._reacquire_port()          # ditto — the board may have moved
        code, out, err = self.connection.run(f"rnodeconf {self.port} --info")
        ok = "Device signature" in out and "Firmware version" in out
        if not ok and "Device info" in (out or "") and "KeyError" in (err or "") + (out or ""):
            # the board ANSWERED (its product/model came back) but this medic's
            # stock rnodeconf has no name for the medic's custom product code
            # 0xcb and crashes while printing it. Node Medic 1's tool was
            # patched by hand; a clone's is not (Node Medic 2, 2026-10-06).
            # The firmware hash check below is the real validation.
            ok = True
        hash_note = ""
        if ok:
            hash_note = self._check_and_cure_firmware_hash()
        if ok:
            # An RNode has no Reticulum identity, so its birth record is keyed
            # by the board's USB fingerprint — how the medic recognises it as
            # kin when it's plugged in again.
            r = self.radio
            self.birth_certificate = {
                "node_type": "rnode",
                "board": self.board.display_name,
                "serial_port": self.port,
                "usb_serial": usb_id_for_port(self.connection, self.port),
                "radio": (f"{r.frequency_mhz:g} MHz / BW{r.bandwidth_khz:g} / "
                          f"SF{r.spreading_factor} / CR{r.coding_rate} / "
                          f"{r.tx_power_dbm} dBm") if r else "tool defaults",
            }
        return StepResult(
            "verify", ok,
            ("Board verified as a provisioned RNode." + hash_note) if ok
            else "Board did not report as a valid RNode after flashing.")

    def _check_and_cure_firmware_hash(self) -> str:
        """Catch and cure the zero firmware hash the offline autoinstall
        leaves behind.

        `--autoinstall --nocheck` (every offline birth) skips hash-marking, so
        the stored expectation stays ALL ZEROS: the device's own boot check
        fails, the screen says FIRMWARE CORRUPT, the LEDs sit red — while
        rnodeconf answers politely and the old verify called it healthy. The
        T-Echo Plus shipped exactly that state and its e-paper told on us
        (2026-08-20). Cure: write the DEVICE-COMPUTED hash of the image we
        just flashed from our own cache as the expectation — the same
        trust anchor the RTNode path uses. On nRF52 the write ends in the
        firmware's own hard reset (Device.h), so re-find the port after.
        Returns a note for the verify message; never fails the step (the
        board IS a valid RNode — an uncured hash is reported, not hidden)."""
        kl = self.connection.run(
            f"timeout 45 rnodeconf {self.port} -K -L 2>&1", timeout=60)[1] or ""
        import re as _re
        hashes = _re.findall(r"hash is:\s*\n?\s*([0-9a-f]{64})", kl)
        if len(hashes) < 2:
            return " (firmware-hash state unreadable — check the screen.)"
        target, actual = hashes[0], hashes[1]
        if target == actual:
            return ""
        if target != "0" * 64:
            return (" WARNING: stored and running firmware hashes disagree — "
                    "the board will report FIRMWARE CORRUPT.")
        seth = self.connection.run(
            f"timeout 45 rnodeconf {self.port} --firmware-hash {actual} 2>&1",
            timeout=60)[1] or ""
        if "Firmware hash set" not in seth:
            return (" WARNING: the firmware hash was never marked and could "
                    "not be set — the board will report FIRMWARE CORRUPT.")
        # the hash write hard-resets the board; give it a beat and re-find it
        self.connection.run("sleep 6")
        self._reacquire_port()
        kl2 = self.connection.run(
            f"timeout 45 rnodeconf {self.port} -K -L 2>&1", timeout=60)[1] or ""
        h2 = _re.findall(r"hash is:\s*\n?\s*([0-9a-f]{64})", kl2)
        if len(h2) >= 2 and h2[0] == h2[1]:
            return (" Firmware hash was unmarked (offline install) — set and "
                    "verified; the board's own check now passes.")
        return (" Firmware hash set, but the read-back after the reboot did "
                "not confirm it — check the board's screen.")

    # -- driver ------------------------------------------------------------

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        for step in (self._detect_port, self._ensure_single_board,
                     self._ensure_firmware, self._flash, self._set_params,
                     self._verify):
            result = step()
            self.results.append(result)
            from workflows.step_log import log_step
            log_step(result)
            emit(result)
            if not result.success:
                break
        return self.results
