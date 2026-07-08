"""Radio & firmware diagnostics (checks 12-21).

Talks to the attached RNode board through ``rnodeconf`` to confirm it is
responsive, running current firmware with its hash set, and configured with
the intended LoRa parameters.
"""

from __future__ import annotations

import re
from typing import List

from node_profile import NodeHardware
from diagnostics.base import DiagnosticCheck, Fix, Issue

#: Latest RNode firmware version this tool ships / expects.
LATEST_FIRMWARE = "1.80"


class RadioFirmwareCheck(DiagnosticCheck):
    category_name = "Radio & firmware"

    def _rnode_info(self) -> str:
        port = self.profile.radio.serial_port
        return self._cmd_output(f"rnodeconf {port} --info")

    def run(self) -> List[Issue]:
        r = self.profile.radio
        info = self._rnode_info()
        issues = []

        # 12
        issues.append(self._check(
            "serial_responsive", bool(info),
            "The RNode board is not responding over serial.",
            severity="critical"))
        # 13
        issues.append(self._check(
            "firmware_present", "Firmware version" in info,
            "No RNode firmware was detected on the board.",
            severity="critical"))
        # 14
        issues.append(self._check(
            "firmware_hash_set", "Firmware hash" in info,
            "The firmware hash is not set — Reticulum may refuse to use this "
            "board.",
            severity="warning", auto_fixable=True,
            fix_description="Set the firmware hash with rnodeconf."))
        # 15
        issues.append(self._check(
            "firmware_version_current",
            f"Firmware version: {LATEST_FIRMWARE}" in info,
            f"The RNode firmware is out of date (latest is {LATEST_FIRMWARE}).",
            severity="warning"))
        # 16
        issues.append(self._check(
            "frequency", f"{r.frequency_mhz} MHz" in info,
            f"The radio frequency does not match {r.frequency_mhz} MHz.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        # 17
        issues.append(self._check(
            "bandwidth", f"{r.bandwidth_khz} KHz" in info,
            f"The radio bandwidth does not match {r.bandwidth_khz} kHz.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        # 18
        issues.append(self._check(
            "spreading_factor",
            f"Spreading factor: {r.spreading_factor}" in info,
            f"The spreading factor does not match SF{r.spreading_factor}.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        # 19
        issues.append(self._check(
            "coding_rate", f"Coding rate: {r.coding_rate}" in info,
            f"The coding rate does not match CR{r.coding_rate}.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        # 20
        issues.append(self._check(
            "tx_power", f"TX power: {r.tx_power_dbm} dBm" in info,
            f"The TX power does not match {r.tx_power_dbm} dBm.",
            severity="critical", auto_fixable=True,
            fix_description="Re-apply the radio parameters with rnodeconf."))
        # 21
        port = r.serial_port
        loop_ok = self._run_cmd(f"rnodeconf {port} --loop")[0] == 0
        issues.append(self._check(
            "radio_loopback", loop_ok,
            "The radio failed its serial loopback (L1) test.",
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

        # 86 serial data-capable (charge-only cables open a port but pass no data)
        issues.append(self._check(
            "serial_data_capable",
            bool(self._cmd_output(f"rnodeconf {port} --version").strip()),
            "The serial port opens but passes no data — likely a charge-only "
            "USB cable.",
            severity="critical"))

        # 87 antenna pre-transmit warning (anomalous noise floor)
        m = re.search(r"Noise floor:\s*(-?\d+)", info)
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

    def _fix_handlers(self):
        param_fix = self._apply_radio_params
        return {
            "firmware_hash_set": self._set_firmware_hash,
            "frequency": param_fix,
            "bandwidth": param_fix,
            "spreading_factor": param_fix,
            "coding_rate": param_fix,
            "tx_power": param_fix,
            "flow_control_atmega": self._fix_flow_control,
            "modemmanager_interference": self._fix_modemmanager,
        }

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

    def _set_firmware_hash(self, issue: Issue) -> Fix:
        r = self.profile.radio
        code, out, err = self._run_cmd(
            f"rnodeconf {r.serial_port} --set-firmware-hash")
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=("Firmware hash set" if ok
                            else f"Could not set hash: {err or out}"),
                   raw_output=out)
