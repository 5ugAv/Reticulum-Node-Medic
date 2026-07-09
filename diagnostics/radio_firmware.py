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

#: Latest RNode firmware version this tool ships / expects (verified on real
#: hardware — rnodeconf --info reports e.g. "Firmware version   : 1.86").
LATEST_FIRMWARE = "1.86"


def _ver_tuple(v: str):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


class RadioFirmwareCheck(DiagnosticCheck):
    category_name = "Radio & firmware"

    def _rnode_info(self) -> str:
        # NOTE: rnodeconf can only open the RNode serial when rnsd is NOT holding
        # it (build/maintenance). On a live transport node the radio state comes
        # from rnstatus --json instead.
        port = self.profile.radio.serial_port
        return self._cmd_output(f"rnodeconf {port} --info")

    @staticmethod
    def _info_str(info: str, pattern: str):
        """Extract a labelled field from rnodeconf --info. Real format uses
        aligned columns with a space before the colon, e.g.
        ``\tSpreading factor : 11`` — so patterns must allow ``\\s*:``."""
        m = re.search(pattern, info)
        return m.group(1) if m else None

    def run(self) -> List[Issue]:
        r = self.profile.radio
        port = r.serial_port
        info = self._rnode_info()
        has_info = bool(info)
        issues = []

        # 12
        issues.append(self._check(
            "serial_responsive", has_info,
            "The RNode board is not responding over serial.",
            severity="critical"))
        # 13 firmware present
        issues.append(self._check(
            "firmware_present", "Firmware version" in info,
            "No RNode firmware was detected on the board.",
            severity="critical"))
        # 14 device signature verified. Real --info shows "Device signature :
        # Verified/Unverified" — there is NO "Firmware hash" line. When info is
        # present but the field is missing (format drift), we pass rather than
        # false-positive; a silent/absent board fails via has_info.
        sig = self._info_str(info, r"Device signature\s*:\s*(\w+)")
        issues.append(self._check(
            "firmware_hash_set", has_info and (sig is None or sig == "Verified"),
            "The RNode's firmware signature is unverified — reflash from a "
            "trusted binary to make it verifiable.",
            severity="warning", auto_fixable=True,
            fix_description="Re-flash to a verifiable state "
                            "(rnodeconf <port> --autoinstall)."))
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
