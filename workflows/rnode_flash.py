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


#: ``usb-<vendor>_<product>_<SERIAL>-if00`` — the tail is the board's stable
#: hardware serial, the ONE thing that survives a re-enumeration.
_BY_ID_SERIAL = re.compile(r"_([^_]+)-if\d+$")


def by_id_serial(by_id_name):
    """The hardware serial out of a /dev/serial/by-id basename, or None.

    The rest of the name is NOT stable across a flash: the RAK4631 announces
    itself as ``RAKWireless_WisBlock_RAK4631`` from its bootloader and
    ``RAKwireless_WisBlock_RAK4631`` once running RNode firmware — a capital W
    becomes lowercase. Only the serial (``4631000000000002``) is constant.
    """
    m = _BY_ID_SERIAL.search(by_id_name or "")
    return m.group(1) if m else None


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
    found = find_port_by_usb_serial(connection, serial)
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

    def _detect_port(self) -> StepResult:
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
        return StepResult("detect_port", True, f"Board on {port}.")

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
        if self.board.flash_method != "autoinstall":
            # custom fork: the medic's own build is the firmware source
            if self.connection.run(
                    f"test -f {TRACKER_BUILD_DIR}/RNode_Firmware.ino.bin")[0] != 0:
                return StepResult(
                    "ensure_firmware", False,
                    "The medic's Tracker fork build is missing — rebuild it "
                    "before flashing this board.")
            return StepResult("ensure_firmware", True,
                              "Custom fork firmware ready (the medic's own "
                              "proven build).")
        if has_connectivity(self.connection):
            res = sync_firmware(self.connection)
            if res.failed:
                return StepResult("ensure_firmware", False,
                                  f"Firmware sync failed for "
                                  f"{', '.join(res.failed[:3])}.")
            return StepResult("ensure_firmware", True,
                              f"Firmware ready ({res.message}).")
        # Offline: the carried cache must already hold this version.
        if self.connection.run(f"ls {RNODE_UPDATE_DIR}/{self.version}/*.zip")[0] != 0:
            return StepResult("ensure_firmware", False,
                              f"Offline and no firmware {self.version} cached. "
                              f"Connect WiFi once to seed the cache.")
        return StepResult("ensure_firmware", True,
                          f"Offline — using cached firmware {self.version}.")

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
        if not ok and not already:
            # A board that arrives carrying FOREIGN firmware (factory image,
            # Meshtastic, a half-written flash) can present a USB serial port
            # while speaking nothing rnodeconf understands, and autoinstall
            # then dies at its post-write probe — "Serial port opened, but
            # RNode did not respond" (live: a fresh T-Beam Supreme,
            # 2026-08-01). The RTNode path never hits this because it ERASES
            # first. Do the same here, once, then retry: on a truly blank chip
            # autoinstall has nothing to be confused by.
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
        d = TRACKER_BUILD_DIR
        code, out, err = self.connection.run(
            f"python3 {TRACKER_ESPTOOL} --chip esp32s3 --port {self.port} "
            f"--baud 115200 --no-stub write_flash -z --flash_size 8MB "
            f"0x0 {d}/RNode_Firmware.ino.bootloader.bin "
            f"0x8000 {d}/RNode_Firmware.ino.partitions.bin "
            f"0xe000 {TRACKER_BOOT_APP0} "
            f"0x10000 {d}/RNode_Firmware.ino.bin",
            timeout=self.flash_timeout)
        low = (out or "").lower()
        if code != 0 and "hash of data verified" not in low:
            return StepResult("flash", False,
                              f"esptool write failed: {(err or out)[-200:]}")
        # boot, then ROM-bootstrap as the custom product (cb/ca for the
        # Tracker) — signed with the medic's project key.
        p = self.board.provision or {}
        code, out, err = self.connection.run(
            f"sleep 4 && rnodeconf {self.port} -r "
            f"--product {p.get('product', 'cb')} "
            f"--model {p.get('model', 'ca')} "
            f"--platform {p.get('platform', '0x80')} "
            f"--hwrev {p.get('hwrev', '1')}",
            timeout=self.flash_timeout)
        low = ((out or "") + (err or "")).lower()
        if "bootstrapping successful" not in low and "signature validated" not in low:
            return StepResult("flash", False,
                              f"Flashed, but EEPROM bootstrap failed: "
                              f"{(err or out)[-200:]}")
        # firmware hash = the app image's embedded SHA (validates, not corrupt)
        from workflows.rnode_v4_rgb import embedded_hash_command
        self.connection.run(
            embedded_hash_command(self.port, f"{d}/RNode_Firmware.ino.bin"),
            timeout=120)
        return StepResult(
            "flash", True,
            f"Flashed the medic's proven Tracker fork image and provisioned "
            f"as {self.board.display_name}.")

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
        out = self.connection.run(f"rnodeconf {self.port} --info")[1]
        ok = "Device signature" in out and "Firmware version" in out
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
