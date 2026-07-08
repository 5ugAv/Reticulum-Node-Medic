"""RTNode-2400 diagnostics (Type B: standalone Heltec V4, 5ugAv fork).

The RTNode-2400 has **no text console** — on serial it speaks the RNode KISS
binary protocol plus emits passive human-readable log output. Among that log
is a beacon line the firmware prints for every announce:

    [HealthBeacon] announce dst=<32hex dest hash> data=<28hex 14-byte payload>

So Type B health here is beacon-first: we capture recent serial output, pull
the beacon's ``data=`` payload, decode it with the shared ``health_beacon``
codec (the same wire contract used over the mesh), and derive checks from the
decoded fields. The boot log — also passive — is scanned for FATAL.

Known-issue awareness (fork status): heap leak under persistent TCP (surfaced
by the fault bit / low heap), WiFi lockup under weak signal (wifi flags +
RSSI), watchdog-armed confirmation, and the V3 PSRAM limitation (flagged, not
a fault). A physical visit gets an immediate beacon by power-cycling the board
(a beacon fires ~30 s after boot).
"""

from __future__ import annotations

import re
from typing import List, Optional

from diagnostics.base import DiagnosticCheck, Issue
from monitor.health_beacon import HealthBeacon, BOARD_IDS, decode

#: Pseudo-command the serial transport interprets as "return buffered log".
CAPTURE_COMMAND = "rnm-serial-capture"

_BEACON_RE = re.compile(r"\[HealthBeacon\][^\n]*data=([0-9a-fA-F]+)")

HEAP_WARN_KB = 40
WIFI_WARN_DBM = -75
WIFI_ALERT_DBM = -85
HELTEC_V3_ID = 0x3A
ABNORMAL_RESETS = {1, 2, 3}  # panic, brownout, task_wdt


class RTNode2400Check(DiagnosticCheck):
    category_name = "RTNode-2400"

    def _serial_log(self) -> str:
        return self._cmd_output(CAPTURE_COMMAND)

    def _parse_beacon(self, log: str) -> Optional[HealthBeacon]:
        m = _BEACON_RE.search(log)
        if not m:
            return None
        try:
            return decode(bytes.fromhex(m.group(1)))
        except ValueError:
            return None

    def run(self) -> List[Issue]:
        log = self._serial_log()
        beacon = self._parse_beacon(log)
        issues: List[Optional[Issue]] = []

        # From the passive log (independent of the beacon).
        issues.append(self._check(
            "beacon_received", beacon is not None,
            "No decodable health beacon on serial — the node may not be "
            "beaconing, or the USB cable is charge-only.",
            severity="critical"))
        # Real firmware markers: a startup "FATAL" assertion, or the watchdog's
        # heap-floor reboot line "[WATCHDOG] CRITICAL: ... REBOOTING".
        fatal = ("FATAL" in log
                 or "[WATCHDOG] CRITICAL" in log
                 or "REBOOTING" in log)
        issues.append(self._check(
            "boot_fatal", not fatal,
            "The boot log shows a fatal/critical event (startup assertion, "
            "interface name collision, or a watchdog heap-floor reboot).",
            severity="critical"))

        if beacon is None:
            return [i for i in issues if i is not None]

        # From the decoded beacon.
        issues.append(self._check(
            "lora_link", beacon.lora_up,
            "The LoRa radio is down — this node is off the mesh.",
            severity="critical"))
        issues.append(self._check(
            "heap_fault", not beacon.fault,
            "The node is reporting a fault: internal-SRAM heap pressure below "
            "the early-warning threshold (the TCP heap-leak breach).",
            severity="critical"))
        issues.append(self._check(
            "heap_low", beacon.free_heap_kb >= HEAP_WARN_KB,
            f"Free heap is low ({beacon.free_heap_kb} KB low-water) — watch for "
            f"the TCP heap leak.",
            severity="warning"))
        issues.append(self._check(
            "watchdog_armed", beacon.wdt_armed,
            "The hardware watchdog is not armed.",
            severity="warning"))
        issues.append(self._check(
            "wifi_link", beacon.wifi_up,
            "WiFi is down — the node has lost its WAN/config path (and risks "
            "the weak-signal lockup).",
            severity="warning"))

        if beacon.wifi_up and beacon.wifi_rssi_dbm <= WIFI_WARN_DBM:
            issues.append(Issue(
                check_name="wifi_rssi",
                category=self.category_name,
                description=f"WiFi signal is weak ({beacon.wifi_rssi_dbm} dBm) — "
                            f"risk of the lockup that takes down WiFi+BT+LoRa.",
                severity="critical" if beacon.wifi_rssi_dbm <= WIFI_ALERT_DBM
                else "warning",
                raw_detail=f"{beacon.wifi_rssi_dbm} dBm",
            ))

        issues.append(self._check(
            "tcp_backbone", beacon.tcp_backbone_up,
            "The TCP backbone link (LoRa->WAN bridge) is down.",
            severity="info"))
        issues.append(self._check(
            "local_tcp_server", beacon.local_tcp_server_up,
            "The local TCP server is not accepting LAN clients.",
            severity="info"))

        if beacon.reset_reason in ABNORMAL_RESETS:
            issues.append(Issue(
                check_name="abnormal_reset",
                category=self.category_name,
                description=f"The node last reset abnormally "
                            f"({beacon.reset_reason_label}).",
                severity="warning",
                raw_detail=beacon.reset_reason_label,
            ))

        # V3 PSRAM is stability-limited — a known hardware limitation, not a fault.
        issues.append(self._check(
            "psram_v3_note", beacon.board_id != HELTEC_V3_ID,
            "This is a Heltec V3 — its PSRAM is stability-limited. Known "
            "hardware limitation, not a fault.",
            severity="info"))

        issues.append(self._check(
            "board_identified", beacon.board_id in BOARD_IDS,
            f"Unrecognised board id 0x{beacon.board_id:02x}.",
            severity="info"))

        return [i for i in issues if i is not None]
