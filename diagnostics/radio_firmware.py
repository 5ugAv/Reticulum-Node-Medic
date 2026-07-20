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
        info = self._cmd_output(f"rnodeconf {port} --info")
        if self._device_read(info):
            return info
        listing = self._cmd_output("ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")
        for p in listing.split():
            if p == port:
                continue
            alt = self._cmd_output(f"rnodeconf {p} --info")
            if self._device_read(alt):
                self.profile.radio.serial_port = p     # remember the real port
                return alt
        return info

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
        if (not has_info and self._service_is_active("rnsd")
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
        # 14 firmware hash matches the running firmware. A board can have a VALID
        # EEPROM + a validated device signature yet display "firmware corrupt":
        # that happens when the firmware hash stamped in the EEPROM differs from
        # the hash the firmware computes for itself at boot (e.g. an app flashed
        # without restamping the hash, or a firmware/hash mismatch after a mixed
        # flash). rnodeconf --info does NOT reveal this, so we read the device's
        # stored hash vs its computed target directly (fw_hash_probe). Only
        # meaningful once the board has firmware + a valid EEPROM — otherwise the
        # serial/firmware/eeprom checks above already own the diagnosis.
        if has_info and "EEPROM is invalid" not in info:
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
        code, out, err = self._run_cmd(
            f"rnodeconf {port} --autoinstall", timeout=400)
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=("Reprovisioned the EEPROM via autoinstall." if ok
                            else f"autoinstall failed: {(err or out)[-200:]}\n\n"
                                 f"{FLASH_RECOVERY}"),
                   raw_output=out)

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

