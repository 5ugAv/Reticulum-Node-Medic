"""RTNode-2400 diagnostics (checks 64-73, 92).

For the standalone Heltec WiFi LoRa32 V4 running the 5ugAv microReticulum fork
(Type B node — no Pi). The device is queried over its serial/console interface
which returns a status blob and a boot log. Known-issue awareness:

- Fixed in the fork: TCP reconnect busy-loop, LAN->WAN boundary leak, clean-
  build failure, first-boot ERROR noise, interface-naming doc bug, NeoPixel
  RGB LED, startup FATAL on interface name collision.
- Still open: heap leak under persistent TCP, WiFi lockup under weak signal,
  watchdog not confirmed armed.
- Not firmware-fixable: V3 PSRAM limitation — detect and flag, not a fault.
"""

from __future__ import annotations

import re
from typing import List, Optional

from diagnostics.base import DiagnosticCheck, Fix, Issue


class RTNode2400Check(DiagnosticCheck):
    category_name = "RTNode-2400"

    def _status(self) -> str:
        return self._cmd_output("rtnode --status")

    def _bootlog(self) -> str:
        return self._cmd_output("rtnode --bootlog")

    def run(self) -> List[Issue]:
        status = self._status()
        bootlog = self._bootlog()
        is_fork = "5ugAv" in status
        is_v3 = "8MB" in status
        issues: List[Optional[Issue]] = []

        # 64 board variant (8MB = V3, 16MB = V4)
        issues.append(self._check(
            "board_variant", "8MB" in status or "16MB" in status,
            "Could not determine the board variant (flash size).",
            severity="info"))

        # 65 V3 PSRAM limitation (flag, not a fault)
        issues.append(self._check(
            "psram_v3_limited", not is_v3,
            "This is a V3 board — its PSRAM is stability-limited. This is a "
            "known hardware limitation, not a fault.",
            severity="info"))

        # 66 heap trend (leak under persistent TCP)
        m = re.search(r"Heap min:\s*(\d+)", status)
        heap_min = int(m.group(1)) if m else None
        issues.append(self._check(
            "heap_trend", heap_min is None or heap_min > 20000,
            "Free heap is trending dangerously low — likely the TCP heap leak "
            "(~1-2 h crash cycle).",
            severity="warning"))

        # 67 watchdog armed
        issues.append(self._check(
            "watchdog_armed", "Watchdog: armed" in status,
            "The hardware watchdog is not confirmed armed.",
            severity="warning"))

        # 68 boot-log FATAL scan
        issues.append(self._check(
            "boot_fatal", "FATAL" not in bootlog,
            "The boot log contains a FATAL error (e.g. a startup assertion or "
            "interface name collision).",
            severity="critical"))

        # 69 WiFi RSSI (orange < -75, red < -85)
        m = re.search(r"WiFi RSSI:\s*(-?\d+)", status)
        rssi = int(m.group(1)) if m else None
        if rssi is not None and rssi < -75:
            issues.append(Issue(
                check_name="wifi_rssi",
                category=self.category_name,
                description=f"WiFi signal is weak ({rssi} dBm) — risk of the "
                            f"WiFi lockup that takes down WiFi+BT+LoRa.",
                severity="critical" if rssi < -85 else "warning",
                raw_detail=f"{rssi} dBm",
            ))

        # 70 fork verification (must be the 5ugAv fork, not upstream)
        issues.append(self._check(
            "fork_verification", is_fork,
            "This board is running upstream microReticulum, not the 5ugAv "
            "fork — it is missing critical fixes.",
            severity="warning"))

        # 71 interface name collision (pre-flash)
        collide = self._cmd_output("rtnode --check-interfaces")
        issues.append(self._check(
            "interface_collision", "COLLISION" not in collide,
            "Two interfaces share a name — this causes a startup FATAL "
            "assertion.",
            severity="warning"))

        # 72 Heltec V3 re-flash failure (auto-retry at a lower baud)
        issues.append(self._check(
            "reflash_failure", "flash: failed" not in status.lower(),
            "The last firmware flash failed — retry at a lower baud rate.",
            severity="warning", auto_fixable=True,
            fix_description="Re-flash at 115200 baud."))

        # 73 first-boot ERROR noise (benign on the fork)
        issues.append(self._check(
            "first_boot_errors", is_fork or "ERROR" not in bootlog,
            "The boot log has ERROR noise on upstream firmware — the fork "
            "silences this benign output.",
            severity="info"))

        # 92 WiFi antenna compressed by the enclosure
        issues.append(self._check(
            "wifi_antenna_compressed", "Antenna: compressed" not in status,
            "The WiFi antenna appears compressed by the enclosure, hurting "
            "signal — reposition it.",
            severity="info"))

        return [i for i in issues if i is not None]

    # -- fixes -------------------------------------------------------------

    def _fix_handlers(self):
        return {"reflash_failure": self._fix_reflash}

    def _fix_reflash(self, issue: Issue) -> Fix:
        port = self.profile.radio.serial_port
        code, out, err = self._run_cmd(
            f"rtnode-flash {port} --baud 115200")
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=("Re-flashed at 115200 baud." if ok
                            else f"Re-flash failed: {err or out}"),
                   raw_output=out)
