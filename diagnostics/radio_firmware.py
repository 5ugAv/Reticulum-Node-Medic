"""Radio & firmware diagnostics (checks 12-21).

Talks to the attached RNode board through ``rnodeconf`` to confirm it is
responsive, running current firmware with its hash set, and configured with
the intended LoRa parameters.
"""

from __future__ import annotations

import os
import re
from typing import List

from node_profile import NodeHardware
from diagnostics.base import DiagnosticCheck, Fix, Issue

#: Carried probe that reads the board's stored firmware hash vs its computed
#: target to detect the "firmware corrupt" state (--info doesn't surface it).
FW_HASH_PROBE_LOCAL = os.path.join(
    os.path.dirname(__file__), os.pardir, "assets", "scripts", "fw_hash_probe.py")
FW_HASH_PROBE_REMOTE = "/tmp/rnm_fw_hash_probe.py"

#: Latest RNode firmware version this tool ships / expects (verified on real
#: hardware — rnodeconf --info reports e.g. "Firmware version   : 1.86").
LATEST_FIRMWARE = "1.86"

#: Settle-retry budget for the firmware-blessing read (-K -L). A board that
#: was just reset leaves a DYING tty lingering on the bus (the _nrf_settle
#: lesson from rtnode_build): the first read after a reset can come back
#: empty or garbled from a port whose board has already moved. Re-resolve and
#: re-read up to 2 laps, 8 s apart, before honestly answering "couldn't
#: check" — 2, not more, because the operator is watching a PROBE screen and
#: the whole budget (pre-settle + laps + one mismatch-confirm read at 30 s
#: per command) must stay under ~2 minutes (adversarial review C7,
#: 2026-08-22).
BLESSING_LAPS = 2
BLESSING_SETTLE_SECONDS = 8
#: One settle beat BEFORE the first -K -L read. Device.h:142 — the firmware's
#: deferred hard reset fires AFTER the previous host command exits, and
#: opening the serial port at all resets hw-CDC ESP32s — so the --info reads
#: just above the blessing check may have JUST rebooted the board, and lap 1
#: would land inside that reset window (adversarial review C5, 2026-08-22).
BLESSING_PRE_SETTLE_SECONDS = 6
#: Columba's war table (mirrored at workflows/rtnode_build.py's hash step): a
#: device mid-glitch reports an ALL-ZEROS running hash. Writing that as the
#: expectation bricks hw_ready on the next boot — Columba logs a warning and
#: writes it anyway; we refuse to even PRESCRIBE it (adversarial review C1,
#: 2026-08-22).
ZERO_HASH = "0" * 64


def _work_board_cleared(connection, port: str) -> bool:
    """C6 — the Jonesey gate for the -K -L read (adversarial review,
    2026-08-22). ``rnodeconf -K -L`` is not a pure read: it writes KISS
    detect frames into the port, and the splitter holds Jonesey WITHOUT
    exclusive=True — so a "read" against the medic's own radio steals bytes
    from the live mesh stream. And ``_rnode_info``'s fallback enumeration can
    re-pin the profile onto any answering port. So before -K -L runs the
    port must be POSITIVELY proven a flashable work board — fail-closed
    ``is_flashable_work_board``: an unresolvable identity is a refusal, not
    a pass.

    Scope: the roster describes the MEDIC'S OWN USB tree. Over SSH the port
    lives on the remote node (where Jonesey is unreachable by definition)
    and a local roster lookup would compare the wrong machine's devices, so
    the gate applies to local connections only. A bare dev host / CI with no
    roster and no by-id tree has no onboard radio to protect
    (``guard_is_active`` — the repo-wide convention)."""
    try:
        from transport.connection import SSHConnection
        if isinstance(connection, SSHConnection):
            return True
        from ui.onboard_roster import guard_is_active, is_flashable_work_board
        if not guard_is_active():
            return True
        return bool(is_flashable_work_board(port))
    except Exception:
        return False        # can't even ask -> refuse (fail closed)

#: When a reflash won't take (esptool "serial data stream stopped" / can't sync),
#: this is the field-tested recovery ladder — surfaced verbatim in the repair
#: result so the operator gets the next move instead of a dead-end error. Order
#: matters: cheapest/most-likely first. (See memory: node-flash-recovery.)
FLASH_RECOVERY = (
    "Flash didn't take. Try, in order:\n"
    "  1. Manual bootloader: on the board hold BOOT/PRG, tap RST, release BOOT, "
    "then run the fix again.\n"
    "  2. Swap to a SHORT, known-good USB DATA cable — charge-only or long/thin "
    "cables cause exactly this 'serial noise/corruption'.\n"
    "  3. Flash the board on Node Medic's OWN USB port instead — the medic can "
    "power-cycle the port for a clean reset, the most reliable recovery.\n"
    "  4. Check power: a board that browns out mid-write corrupts the flash — "
    "give it its own supply."
)


def _ver_tuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


class RadioFirmwareCheck(DiagnosticCheck):
    category_name = "Radio & firmware"

    @staticmethod
    def _device_read(info: str) -> bool:
        """True only if rnodeconf actually reached a device. Verified live:
        rnodeconf exits 0 even on 'Could not open port', so exit status and
        `bool(info)` both lie — 'Device connected' / a firmware line is the real
        signal."""
        return "Device connected" in info or "firmware version" in info.lower()

    def _rnode_info(self) -> str:
        # The profile's default serial port is often wrong (ttyUSB0 vs a real
        # Heltec V4 on ttyACM0), so if it doesn't reach a device, auto-detect:
        # probe each ttyACM*/ttyUSB* until one responds and remember it. (When
        # rnsd is holding the port, none respond — the caller's live-mode gate
        # handles that.)
        port = self.profile.radio.serial_port
        # Retry the target port: a board that was just accessed (flashed, TX'd,
        # hash-probed) can miss the first --info while it settles/re-enumerates,
        # which would otherwise false-trip the live-mode gate below.
        info = ""
        for _ in range(3):
            info = self._cmd_output(f"rnodeconf {port} --info")
            if self._device_read(info):
                return info
        listing = self._cmd_output("ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")
        for p in listing.split():
            if p == port:
                continue
            try:                       # never re-pin onto the medic's own radio
                from ui.onboard_roster import is_onboard
                if is_onboard(p):
                    continue
            except Exception:
                pass
            alt = self._cmd_output(f"rnodeconf {p} --info")
            if self._device_read(alt):
                self.profile.radio.serial_port = p     # remember the real port
                return alt
        return info

    def _port_held(self, port: str) -> bool:
        """True if *port* is held open by another process (rnsd/the splitter) —
        i.e. the medic's OWN radio in live service, not a free work board. Used to
        scope the live-mode gate to the in-service radio only."""
        try:
            return self._run_cmd(f"fuser {port} 2>/dev/null")[0] == 0
        except Exception:
            return False

    @staticmethod
    def _info_str(info: str, pattern: str):
        """Extract a labelled field from rnodeconf --info. Real format uses
        aligned columns with a space before the colon, e.g.
        ``\tSpreading factor : 11`` — so patterns must allow ``\\s*:``."""
        m = re.search(pattern, info)
        return m.group(1) if m else None

    def run(self) -> List[Issue]:
        r = self.profile.radio
        info = self._rnode_info()          # may auto-correct r.serial_port
        port = r.serial_port
        has_info = self._device_read(info)  # NOT bool(info): error text lies
        issues = []

        # Live/service mode gate: if rnsd is running the RNode, it HOLDS the
        # serial port, so the maintenance-mode rnodeconf probes below cannot read
        # the device — they'd false-report "no firmware / not responsive" on a
        # perfectly healthy live node (verified on nodemedic's live rnsd). Report
        # one info instead; live radio health is covered by the Network & mesh
        # checks. A genuinely dead board in MAINTENANCE mode (rnsd stopped) still
        # surfaces normally below.
        # CRUCIAL: only gate when the TARGET port is actually the in-service radio
        # (held open). A free WORK board that didn't read is a real fault to
        # report, not "the medic's radio is busy" — otherwise PROBE of an attached
        # board falsely says radio_in_service just because the medic's own rnsd runs.
        if (not has_info and self._port_held(port)
                and self._service_is_active("rnsd")
                and self._rnode_interface() is not None):
            return [self._check(
                "radio_in_service", False,
                "The radio is in live use by a running rnsd, which holds the "
                "serial port — firmware/parameter checks need maintenance mode "
                "(stop rnsd first). Live radio health is covered by the Network "
                "& mesh checks.",
                severity="info")]

        # 12
        issues.append(self._check(
            "serial_responsive", has_info,
            "The RNode board is not responding over serial.",
            severity="critical"))
        # 13 firmware present. Case-insensitive: a board with a corrupt EEPROM
        # still reports "Current firmware version: 1.86" (lowercase f) even though
        # the "Firmware version : ..." device-info line is hidden. Keying off the
        # capitalised line alone false-reported "no firmware" on a real board
        # whose firmware WAS present (its EEPROM was the actual fault).
        issues.append(self._check(
            "firmware_present", "firmware version" in info.lower(),
            "No RNode firmware was detected on the board.",
            severity="critical"))
        # 13b EEPROM valid / provisioned. A flashed-but-unprovisioned board (or a
        # corrupt EEPROM) reports "EEPROM is invalid": it has firmware but no
        # identity/radio config and won't work as an RNode. Verified on a real
        # faulty board — the specific, actionable diagnosis vs a param cascade.
        issues.append(self._check(
            "eeprom_valid", "EEPROM is invalid" not in info,
            "The RNode's EEPROM is invalid or unprovisioned — it has firmware "
            "but no identity or radio configuration, so it can't work as an "
            "RNode. Re-provision it (rnodeconf --autoinstall, or -r to bootstrap "
            "the EEPROM without reflashing).",
            severity="critical", auto_fixable=True,
            fix_description="Re-provision the RNode's EEPROM."))
        # 14b firmware BLESSING (the parked-radio trap — 2026-08-22 T114 saga,
        # four hours lost). A BIRTHED board stores a blessed firmware hash in
        # EEPROM; when it disagrees with the hash the firmware computes for
        # itself at boot, the boot check quietly refuses TNC mode and parks
        # the radio at frequency 0.000 — while BLE, KISS and `rnodeconf
        # --info` all answer politely and swear the EEPROM is a healthy TNC.
        # The one-second diagnostic is `rnodeconf -K -L`: it prints the
        # TARGET (stored) and ACTUAL (running) hashes; disagreement = parked.
        # Deliberately NOT auto-fixable: the cure writes EEPROM and diagnosis
        # stays read-only — the same caution
        # rnode_flash._check_and_cure_firmware_hash shows by refusing to
        # auto-cure a non-zero mismatch during verify. We report the state
        # and prescribe the exact cure instead.
        #
        # ONE fault, ONE issue (adversarial review C2, 2026-08-22): this and
        # the old fw_hash_probe check (14) read the SAME two device
        # attributes, so they must never both fire — two criticals for one
        # fault, one of them auto-fixing with a DESTRUCTIVE full reflash when
        # a one-line restamp suffices. The blessing read is primary (it can
        # name the exact restamp cure); the probe below is the FALLBACK for
        # when the blessing could not reach a verdict.
        #
        # The whole read runs INSIDE the lazy _check call so the UI's
        # check_start fires before the first (possibly slow) serial command
        # (C7) — see DiagnosticCheck._check.
        blessing: dict = {}
        gated_on = has_info and "EEPROM is invalid" not in info
        if gated_on:
            def _bless_verdict():
                # Jonesey gate FIRST — before anything touches the port (C6).
                if not _work_board_cleared(self.connection, port):
                    blessing["state"] = "unverified"
                    blessing["rawp"] = port
                    return False
                (blessing["state"], blessing["target"], blessing["actual"],
                 blessing["rawp"], blessing["detail"]) = \
                    self._firmware_blessing(port)
                return blessing["state"] == "blessed"

            issues.append(self._check(
                "firmware_blessing", _bless_verdict,
                lambda: self._blessing_description(blessing),
                severity=lambda: ("critical"
                                  if blessing.get("state") == "parked"
                                  else "info"),
                raw_detail=lambda: self._blessing_raw_detail(blessing)))
        # 14 fallback: the carried fw_hash_probe (a different read mechanism)
        # — ONLY when the blessing read reached no verdict. Never after a
        # guard refusal ("unverified"): the probe opens the same port the
        # gate just refused to touch. A mismatch found HERE has no actual
        # hash to prescribe, so the reflash cure is the honest offer.
        if gated_on and blessing.get("state") == "unreadable":
            hash_status = self._firmware_hash_status(port)
            issues.append(self._check(
                "firmware_hash_valid", hash_status != "mismatch",
                "The RNode reports 'firmware corrupt': the firmware hash stored "
                "in its EEPROM doesn't match the firmware actually running on it, "
                "so it won't operate as an RNode. Re-flash to restore a matching, "
                "verifiable firmware (a Heltec V4 gets the full NeoPixel reflash).",
                severity="critical", auto_fixable=True,
                fix_description="Re-flash the firmware and restamp its hash."))
        # 15 firmware version current (real: "Firmware version   : 1.86")
        fw = self._info_str(info, r"Firmware version\s*:\s*([\d.]+)")
        cur_ok = has_info and (fw is None
                               or _ver_tuple(fw) >= _ver_tuple(LATEST_FIRMWARE))
        issues.append(self._check(
            "firmware_version_current", cur_ok,
            f"The RNode firmware is out of date (have {fw}, latest "
            f"{LATEST_FIRMWARE}).",
            severity="warning"))

        # 16-20 configured LoRa params. Real --info aligns "Label : value" with
        # a space before the colon, so parse with \s*: and compare the value.
        def _num(pattern):
            v = self._info_str(info, pattern)
            try:
                return float(v) if v is not None else None
            except ValueError:
                return None

        # NB: avoid the "Frequency range : ..." and "Max TX power : ..." header
        # lines — match only the per-mode config values.
        freq = _num(r"Frequency\s*:\s*([\d.]+)\s*MHz")
        issues.append(self._check(
            "frequency", has_info and (freq is None or freq == r.frequency_mhz),
            f"The radio frequency is {freq} MHz, not {r.frequency_mhz} MHz.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        bw = _num(r"Bandwidth\s*:\s*([\d.]+)\s*KHz")
        issues.append(self._check(
            "bandwidth", has_info and (bw is None or bw == r.bandwidth_khz),
            f"The radio bandwidth is {bw} kHz, not {r.bandwidth_khz} kHz.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        sf = _num(r"Spreading factor\s*:\s*(\d+)")
        issues.append(self._check(
            "spreading_factor",
            has_info and (sf is None or int(sf) == r.spreading_factor),
            f"The spreading factor is SF{int(sf) if sf else '?'}, not "
            f"SF{r.spreading_factor}.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        cr = _num(r"Coding rate\s*:\s*(\d+)")
        issues.append(self._check(
            "coding_rate",
            has_info and (cr is None or int(cr) == r.coding_rate),
            f"The coding rate is CR{int(cr) if cr else '?'}, not "
            f"CR{r.coding_rate}.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        txp = _num(r"(?<!Max )TX power\s*:\s*(\d+)\s*dBm")
        issues.append(self._check(
            "tx_power",
            has_info and (txp is None or int(txp) == r.tx_power_dbm),
            f"The TX power is {int(txp) if txp else '?'} dBm, not "
            f"{r.tx_power_dbm} dBm.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        # 21 L1 serial link — the board responded to rnodeconf with a populated
        # info block. rnodeconf has no --loop flag.
        issues.append(self._check(
            "radio_loopback", has_info,
            "The radio did not respond over serial (L1).",
            severity="critical"))

        # --- extended checks (57-60, 86-88) ------------------------------
        hw = self.profile.hardware

        # 57 flow control on homebrew ATmega
        issues.append(self._check(
            "flow_control_atmega",
            "ATmega" not in info or "Flow control: enabled" in info,
            "This homebrew ATmega board needs hardware flow control enabled.",
            severity="warning", auto_fixable=True,
            fix_description="Enable flow control with rnodeconf."))

        # 58 ModemManager interference
        issues.append(self._check(
            "modemmanager_interference",
            not self._service_is_active("ModemManager"),
            "ModemManager is running and will grab the radio serial port, "
            "corrupting communication.",
            severity="critical", auto_fixable=True,
            fix_description="Mask ModemManager so it cannot claim the port."))

        # 59 Heltec V3 vs V4 baud rate
        issues.append(self._check(
            "heltec_baud",
            "Serial baud rate" not in info or "Serial baud rate: 115200" in info,
            "The serial baud rate does not match the expected 115200 for this "
            "Heltec board.",
            severity="warning"))

        # 60 Heltec hardware revision (V4.2 vs V4.3)
        issues.append(self._check(
            "heltec_hw_revision",
            hw is not NodeHardware.HELTEC_V4 or "Hardware revision" in info,
            "Could not read the Heltec hardware revision (V4.2 and V4.3 differ).",
            severity="info"))

        # 86 serial data-capable. A charge-only USB cable (or wrong port) lets
        # the device node exist but no device data flows. rnodeconf has no
        # --version device probe, so the real signal is: the port node is
        # present yet --info came back empty. If the node is absent entirely,
        # serial_port_exists/serial_responsive own that — don't double-report.
        port_node = self._run_cmd(f"test -c {port}")[0] == 0
        issues.append(self._check(
            "serial_data_capable",
            (not port_node) or has_info,
            "The serial port exists but the device returned no data — likely a "
            "charge-only USB cable or the wrong port.",
            severity="critical"))

        # 87 antenna pre-transmit warning (anomalous noise floor). The live
        # noise floor comes from rnstatus --json (RNodeInterface.noise_floor);
        # rnodeconf --info does not report it. Fall back to an info regex only
        # for offline/emulated cases.
        iface = self._rnode_interface()
        floor = iface.get("noise_floor") if iface else None
        if floor is None:
            m = re.search(r"[Nn]oise floor\s*:\s*(-?\d+)", info)
            floor = int(m.group(1)) if m else None
        issues.append(self._check(
            "antenna_rssi",
            floor is None or floor <= -50,
            "The noise floor is anomalously high — the antenna may be missing "
            "or disconnected. Do not transmit.",
            severity="warning"))

        # 88 Heltec V4 dual antenna ports (reminder)
        issues.append(self._check(
            "heltec_v4_dual_antenna",
            hw is not NodeHardware.HELTEC_V4,
            "Heltec V4 has two antenna ports — confirm the LoRa antenna is on "
            "the LoRa (not the Wi-Fi) port.",
            severity="info"))

        return [i for i in issues if i is not None]

    # -- fixes -------------------------------------------------------------

    def _firmware_hash_status(self, port: str) -> str:
        """``'match'`` / ``'mismatch'`` / ``'unknown'`` — whether the board's
        stored firmware hash matches the firmware actually running (the "firmware
        corrupt" state). Reads both hashes via the carried fw_hash_probe, since
        rnodeconf --info doesn't surface them. Fails safe to ``'unknown'`` (never
        flags a fault we couldn't actually confirm)."""
        try:
            if not self.connection.push_file(FW_HASH_PROBE_LOCAL,
                                             FW_HASH_PROBE_REMOTE):
                return "unknown"
        except Exception:
            return "unknown"
        out = self._cmd_output(f"python3 {FW_HASH_PROBE_REMOTE} {port}")
        if "FWHASH:MISMATCH" in out:
            return "mismatch"
        if "FWHASH:MATCH" in out:
            return "match"
        return "unknown"

    def _blessing_anchor(self, port: str) -> str:
        """The stable /dev/serial/by-id link currently pointing at *port*, or
        ``''``. The anchor is what makes per-lap re-resolution REAL
        (adversarial review C3, 2026-08-22): production callers hand this
        check a raw /dev/ttyACM* node, and after a reset the board can
        re-enumerate to a DIFFERENT number while the dying tty lingers —
        the raw name alone can never follow it. The by-id link is stable
        across re-enumeration, so we find it once and readlink it every
        lap. On a host with no by-id tree (emulated / CI) there is nothing
        to anchor to and the raw port is used as given."""
        if "/by-id/" in port or "/by-path/" in port:
            return port
        out = self._run_cmd(
            'for l in /dev/serial/by-id/*; do '
            f'if [ "$(readlink -f "$l")" = "{port}" ]; '
            'then echo "$l"; break; fi; done')[1] or ""
        link = out.strip().splitlines()[0].strip() if out.strip() else ""
        return link if link.startswith("/dev/serial/") else ""

    def _raw_tty(self, port: str, anchor: str = "") -> str:
        """The RAW /dev/tty node for *port*, re-resolved through *anchor* —
        and NEVER a by-id symlink handed to rnodeconf (the by-id nRF52 trap:
        rnodeconf rescans the symlink, can match None after a
        re-enumeration, and then touches the WRONG device — it once
        provisioned the Pi's OWN UART while reporting success). Falls back
        to *port* unchanged if nothing resolves."""
        link = anchor or (port if "/by-id/" in port or "/by-path/" in port
                          else "")
        if not link:
            return port
        code, out, _ = self._run_cmd(f"readlink -f {link}")
        real = (out or "").strip()
        if code == 0 and real.startswith("/dev/tty"):
            return real
        return port

    def _read_blessing_hashes(self, rawp: str):
        """One ``rnodeconf -K -L`` read: ``(target, actual, raw_tail)``, with
        ``('', '', tail)`` when nothing trustworthy came back. Parsing
        mirrors rnode_flash._check_and_cure_firmware_hash. An ALL-ZEROS
        actual counts as unreadable, never as a hash: Columba's war table
        (mirrored at rtnode_build's hash step) — a device mid-glitch
        returns all zeros, and prescribing THAT as the expectation bricks
        hw_ready on the next boot. They log a warning and write it anyway;
        we refuse to even prescribe (C1)."""
        out = self._run_cmd(
            f"timeout 30 rnodeconf {rawp} -K -L 2>&1", timeout=40)[1] or ""
        tail = out.strip()[-200:]
        hashes = re.findall(r"hash is:\s*\n?\s*([0-9a-f]{64})", out)
        if len(hashes) < 2 or hashes[1] == ZERO_HASH:
            return "", "", tail
        return hashes[0], hashes[1], tail

    def _firmware_blessing(self, port: str):
        """``(state, target, actual, raw_port, detail)`` where state is
        ``'blessed'`` (stored and running hashes agree), ``'parked'`` (they
        disagree — the boot check has parked the radio at 0.000) or
        ``'unreadable'``.

        Read-only by design: it never writes; the cure is prescribed to the
        operator. Retry discipline:

        * one settle beat BEFORE the first read — the --info reads just ran,
          and Device.h:142's deferred reset (or the port-open reset on
          hw-CDC ESP32s) may still be landing (C5);
        * an unreadable lap settles and re-reads, re-resolving the raw port
          through the by-id anchor EVERY lap because dying ttys linger and
          numbers move (the _nrf_settle lesson);
        * a MISMATCH is never committed on one read (C4): settle, read
          again, and only two consecutive reads agreeing on the same pair
          earn PARKED — mirroring rnode_flash's read-back-before-believing.
          A confirm read that disagrees with the first is an unstable state
          and reports UNREADABLE, honestly."""
        anchor = self._blessing_anchor(port)
        rawp = self._raw_tty(port, anchor)
        detail = ""
        self._run_cmd(f"sleep {BLESSING_PRE_SETTLE_SECONDS}")
        for lap in range(BLESSING_LAPS):
            if lap:
                self._run_cmd(f"sleep {BLESSING_SETTLE_SECONDS}")
            rawp = self._raw_tty(port, anchor)   # re-resolve; ttys move
            target, actual, detail = self._read_blessing_hashes(rawp)
            if not target:
                continue                          # unreadable lap — settle
            if target == actual:
                return "blessed", target, actual, rawp, detail
            # mismatch: confirm before condemning (C4)
            self._run_cmd(f"sleep {BLESSING_SETTLE_SECONDS}")
            rawp = self._raw_tty(port, anchor)
            t2, a2, d2 = self._read_blessing_hashes(rawp)
            if (t2, a2) == (target, actual):
                return "parked", target, actual, rawp, d2
            if t2 and t2 == a2:
                return "blessed", t2, a2, rawp, d2
            return "unreadable", "", "", rawp, (d2 or detail)
        return "unreadable", "", "", rawp, detail

    def _blessing_description(self, b: dict) -> str:
        state = b.get("state", "")
        rawp = b.get("rawp", "")
        if state == "parked":
            return (
                "PARKED: the blessed firmware hash stored in this board's "
                "EEPROM does not match the firmware actually running. The "
                "commonest cause is an offline '--autoinstall --nocheck' "
                "birth that never stamped a hash at all; the other is a "
                "reflash outside the medic's birth flow. Either way the boot "
                "check quietly refuses TNC mode and parks the radio at "
                "frequency 0.000, while BLE, KISS and rnodeconf --info all "
                "answer politely and report a healthy TNC. Cure, in order:\n"
                f"  1. rnodeconf {rawp} --firmware-hash {b.get('actual', '')}\n"
                "  2. COLD power cycle the board — unplug ALL power (USB and "
                "battery); a reset or warm reboot is not enough.\n"
                "If it parks again after that, the heavier cure is a full "
                "re-flash through BIRTH, which restamps the hash.")
        if state == "unverified":
            return (
                "Cannot verify this port is a work board — refusing to touch "
                "it. rnodeconf -K -L writes KISS frames into the port, and if "
                "this were the medic's OWN radio that would steal bytes from "
                "the live mesh stream. Its USB identity could not be "
                "positively resolved as a flashable work board (fail-closed: "
                "an unknown board is refused, never guessed at). Replug the "
                "board or check /dev/serial/by-id, then run PROBE again.")
        return (
            "UNREADABLE: couldn't read this board's firmware-blessing state "
            f"(rnodeconf -K -L gave nothing trustworthy in {BLESSING_LAPS} "
            "laps). This is its own honest answer — the medic will not guess "
            "BLESSED or PARKED. Retry with the board settled, or read it by "
            f"hand: rnodeconf {rawp} -K -L")

    def _blessing_raw_detail(self, b: dict) -> str:
        state = b.get("state", "")
        if state == "parked":
            return f"target={b.get('target')} actual={b.get('actual')}"
        if state == "unreadable" and b.get("detail"):
            # P5: carry the trimmed rnodeconf output so "couldn't check"
            # arrives with its evidence, not just the verdict.
            return f"rnodeconf said: {b['detail']}"
        return ""

    def _fix_handlers(self):
        param_fix = self._apply_radio_params
        return {
            "eeprom_valid": self._fix_eeprom,
            # "firmware corrupt" (stored hash != running firmware) is fixed by the
            # same full reflash: a V4 gets the NeoPixel rebirth (which restamps
            # the correct hash), any other board is reflashed via autoinstall.
            "firmware_hash_valid": self._fix_eeprom,
            "frequency": param_fix,
            "bandwidth": param_fix,
            "spreading_factor": param_fix,
            "coding_rate": param_fix,
            "tx_power": param_fix,
            "flow_control_atmega": self._fix_flow_control,
            "modemmanager_interference": self._fix_modemmanager,
        }

    def _fix_eeprom(self, issue: Issue) -> Fix:
        """Reprovision an invalid/unprovisioned EEPROM. A Heltec V4 gets the full
        NeoPixel reflash (reprovision the EEPROM AND restore the RGB firmware in
        one pass — the tool never leaves a V4 on stock firmware); any other RNode
        is reprovisioned via autoinstall, keeping its stock firmware."""
        port = self.profile.radio.serial_port
        if self.profile.hardware is NodeHardware.HELTEC_V4:
            from workflows.rnode_v4_rgb import HeltecV4RGBWorkflow
            results = HeltecV4RGBWorkflow(self.connection, port=port).run_all()
            ok = bool(results) and results[-1].success
            detail = "; ".join(
                f"{r.name}:{'ok' if r.success else 'FAIL'}" for r in results)
            return Fix(
                issue=issue, success=ok,
                message=("Reprovisioned the EEPROM and restored the Heltec V4 "
                         "NeoPixel firmware." if ok
                         else f"V4 RGB reflash failed at "
                              f"{results[-1].name}: {results[-1].message}\n\n"
                              f"{FLASH_RECOVERY}"),
                raw_output=detail)
        # NOT a bare `rnodeconf --autoinstall`: its prompts read a KEYPRESS
        # from the terminal, so with no PTY and no answers it can never
        # succeed and holds the USB port until the timeout (2026-08-01 bug
        # hunt). Drive the proven birth path instead, which uses a PTY
        # locally, pre-fed answers remotely, and the assert_flashable gate.
        board = self._board_for_repair()
        if board is None:
            return Fix(
                issue=issue, success=False,
                message=("Couldn't identify this board, so the EEPROM can't be "
                         "reprovisioned safely from here. Birth it from BIRTH "
                         "(pick the board by its silkscreen), or reflash "
                         f"manually.\n\n{FLASH_RECOVERY}"),
                raw_output="")
        from workflows.rnode_flash import birth_flash
        ok, msg, _already = birth_flash(self.connection, board, port)
        return Fix(issue=issue, success=ok,
                   message=(f"Reprovisioned the EEPROM ({board.display_name}): "
                            f"{msg}" if ok
                            else f"Reprovision failed: {msg}\n\n{FLASH_RECOVERY}"),
                   raw_output=msg)

    def _board_for_repair(self):
        """Identify the attached board for a reprovision. PROBE builds a bare
        NodeProfile whose hardware stays UNKNOWN, so profile.hardware alone
        made the V4 branch dead code (2026-08-01 bug hunt) — read the board
        back from the device instead, falling back to the profile."""
        from workflows.rnode_boards import get_board
        info = (self._rnode_info() or "").lower()
        for needle, key in (("heltec32 v4", "heltec32_v4"),
                            ("heltec v4", "heltec32_v4"),
                            ("heltec32 v3", "heltec32_v3"),
                            ("heltec v3", "heltec32_v3"),
                            ("t-beam", "tbeam"),
                            ("lora32 v2.1", "lora32_v21"),
                            ("lora32 v2", "lora32_v20"),
                            ("rak4631", "rak4631"),
                            ("t-echo", "techo")):
            if needle in info:
                try:
                    return get_board(key)
                except Exception:
                    return None
        if self.profile.hardware is NodeHardware.HELTEC_V4:
            try:
                return get_board("heltec32_v4")
            except Exception:
                return None
        return None

    def _fix_flow_control(self, issue: Issue) -> Fix:
        r = self.profile.radio
        code, out, err = self._run_cmd(
            f"rnodeconf {r.serial_port} --flow-control on")
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=("Enabled hardware flow control." if ok
                            else f"rnodeconf failed: {err or out}"),
                   raw_output=out)

    def _fix_modemmanager(self, issue: Issue) -> Fix:
        code, out, err = self._run_cmd(
            "systemctl mask ModemManager && systemctl stop ModemManager")
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=("Masked ModemManager." if ok
                            else f"Could not mask ModemManager: {err or out}"),
                   raw_output=out)

    def _apply_radio_params(self, issue: Issue) -> Fix:
        r = self.profile.radio
        cmd = (
            f"rnodeconf {r.serial_port} "
            f"--freq {int(r.frequency_mhz * 1_000_000)} "
            f"--bw {int(r.bandwidth_khz * 1000)} "
            f"--sf {r.spreading_factor} "
            f"--cr {r.coding_rate} "
            f"--txp {r.tx_power_dbm}"
        )
        code, out, err = self._run_cmd(cmd)
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=("Re-applied radio parameters" if ok
                            else f"rnodeconf failed: {err or out}"),
                   raw_output=out)

