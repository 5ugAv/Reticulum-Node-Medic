"""Power & hardware diagnostics (checks 22-28).

Temperature, cooling, battery, SD-card health, filesystem writability,
memory headroom and uptime. Some checks only apply when the profile says the
relevant hardware is present.
"""

from __future__ import annotations

import re
from typing import List, Optional

from diagnostics.base import DiagnosticCheck, Issue


class PowerHardwareCheck(DiagnosticCheck):
    category_name = "Power & hardware"

    def _read_int(self, command: str) -> Optional[int]:
        out = self._cmd_output(command)
        for token in out.split():
            try:
                return int(float(token))
            except ValueError:
                continue
        return None

    def run(self) -> List[Issue]:
        p = self.profile
        issues: List[Optional[Issue]] = []

        # 22 CPU temperature (millidegrees C)
        raw = self._read_int("cat /sys/class/thermal/thermal_zone0/temp")
        temp = raw / 1000.0 if raw is not None else None
        if temp is not None and temp > 70:
            issues.append(Issue(
                check_name="cpu_temperature",
                category=self.category_name,
                description=f"The CPU is running hot ({temp:.0f} °C).",
                severity="critical" if temp > 80 else "warning",
                raw_detail=f"{temp:.1f} C",
            ))

        # 23 cooling fan (only if fitted)
        if p.has_cooling_fan:
            rpm = self._read_int(
                "cat /sys/class/hwmon/hwmon0/fan1_input")
            issues.append(self._check(
                "cooling_fan", rpm is not None and rpm > 0,
                "The cooling fan is not spinning.",
                severity="critical"))

        # 24 battery level (only if battery bank fitted)
        if p.has_battery_bank:
            pct = self._read_int("cat /sys/class/power_supply/BAT0/capacity")
            if pct is not None and pct <= 20:
                issues.append(Issue(
                    check_name="battery_level",
                    category=self.category_name,
                    description=f"Battery is low ({pct}%).",
                    severity="critical" if pct <= 10 else "warning",
                    raw_detail=f"{pct}%",
                ))

        # 25 SD card health (dmesg for mmc errors). dmesg is often restricted,
        # so read it privileged; if we still can't read it, report "unverified"
        # (info) rather than silently passing — a denied read is NOT "healthy".
        code, dmesg_out, _ = self._run_cmd(self._priv("dmesg"))
        if code != 0 and not dmesg_out.strip():
            issues.append(Issue(
                check_name="sd_card_health",
                category=self.category_name,
                description="Could not read the kernel log to check the SD card "
                            "(needs privileges) — SD health unverified.",
                severity="info"))
        else:
            has_err = bool(re.search(r"mmc\d+: error|I/O error", dmesg_out,
                                     re.IGNORECASE))
            issues.append(self._check(
                "sd_card_health", not has_err,
                "The SD card is reporting read/write errors — it may be "
                "failing.",
                severity="critical"))

        # 26 filesystem integrity (can we write?)
        code, _, _ = self._run_cmd(
            "touch /var/tmp/.rtt_wtest && rm -f /var/tmp/.rtt_wtest")
        issues.append(self._check(
            "filesystem_integrity", code == 0,
            "The filesystem is read-only — it may have remounted after "
            "corruption.",
            severity="critical"))

        # 27 available memory (<64 MB)
        mem_kb = self._read_int("grep MemAvailable /proc/meminfo")
        mem_mb = mem_kb / 1024.0 if mem_kb is not None else None
        issues.append(self._check(
            "available_memory", mem_mb is None or mem_mb >= 64,
            f"Very little free memory "
            f"({mem_mb:.0f} MB)." if mem_mb is not None else "Low memory.",
            severity="warning"))

        # 28 uptime (<5 min -> recently rebooted, informational)
        up = self._read_int("cat /proc/uptime")
        issues.append(self._check(
            "uptime", up is None or up >= 300,
            "The node rebooted recently (less than 5 minutes ago).",
            severity="info"))

        return [i for i in issues if i is not None]
