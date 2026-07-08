"""System health diagnostics (checks 29-35).

Disk space, clock accuracy, NTP, log rotation, Log2Ram, zombie processes and
the hardware watchdog — the OS-level housekeeping that keeps a headless node
alive for months unattended.
"""

from __future__ import annotations

import re
from typing import List, Optional

from diagnostics.base import DiagnosticCheck, Fix, Issue


class SystemHealthCheck(DiagnosticCheck):
    category_name = "System health"

    def _percent(self, command: str) -> Optional[int]:
        out = self._cmd_output(command)
        m = re.search(r"(\d+)\s*%", out)
        return int(m.group(1)) if m else None

    def run(self) -> List[Issue]:
        issues: List[Optional[Issue]] = []

        # 29 disk space on /
        used = self._percent("df --output=pcent /")
        if used is not None and used > 80:
            issues.append(Issue(
                check_name="disk_space",
                category=self.category_name,
                description=f"The root filesystem is {used}% full.",
                severity="critical" if used > 90 else "warning",
                raw_detail=f"{used}%",
            ))

        # 30 clock drift (chronyc offset, seconds)
        tracking = self._cmd_output("chronyc tracking")
        m = re.search(r"System time\s*:\s*([\d.]+)\s*seconds", tracking)
        drift = float(m.group(1)) if m else 0.0
        issues.append(self._check(
            "clock_drift", drift < 300,
            f"The system clock has drifted by {drift:.0f} seconds.",
            severity="warning"))

        # 31 NTP synchronised
        ntp = self._cmd_output(
            "timedatectl show -p NTPSynchronized --value").strip()
        issues.append(self._check(
            "ntp_sync", ntp == "yes",
            "The clock is not synchronised over NTP.",
            severity="warning"))

        # 32 log rotation configured
        issues.append(self._check(
            "log_rotation", self._run_cmd("test -f /etc/logrotate.conf")[0] == 0,
            "Log rotation is not configured — logs may fill the disk.",
            severity="warning"))

        # 33 Log2Ram active
        issues.append(self._check(
            "log2ram_active", self._service_is_active("log2ram"),
            "Log2Ram is not active — writing logs directly to the SD card "
            "shortens its life.",
            severity="warning"))

        # 34 zombie processes (>5)
        out = self._cmd_output('ps -eo stat= | grep -c "^Z"')
        try:
            zombies = int(out.strip() or "0")
        except ValueError:
            zombies = 0
        issues.append(self._check(
            "zombie_processes", zombies <= 5,
            f"There are {zombies} zombie processes.",
            severity="warning"))

        # 35 hardware watchdog present
        issues.append(self._check(
            "hardware_watchdog", self._run_cmd("test -c /dev/watchdog")[0] == 0,
            "The hardware watchdog is not available — the node cannot "
            "auto-reboot if it hangs.",
            severity="warning"))

        # --- extended checks (61-62, 74-77, 79-80) -----------------------

        # 61 rnsd PATH in unit matches `which rnsd`
        which = self._cmd_output("which rnsd").strip()
        unit = self._cmd_output("systemctl cat rnsd")
        issues.append(self._check(
            "rnsd_unit_path", which == "" or which in unit,
            "The rnsd service points at a different binary than the one on "
            "PATH.",
            severity="warning", auto_fixable=True,
            fix_description="Point the rnsd unit ExecStart at the right binary."))

        # 62 ext4 journal corruption. dmesg is often restricted -> read
        # privileged; a denied read is reported "unverified" (info), not passed.
        code, dmesg_out, _ = self._run_cmd(self._priv("dmesg"))
        if code != 0 and not dmesg_out.strip():
            issues.append(Issue(
                check_name="ext4_journal_corruption",
                category=self.category_name,
                description="Could not read the kernel log to check the "
                            "filesystem (needs privileges) — unverified.",
                severity="info"))
        else:
            corrupt = ("EXT4-fs error" in dmesg_out
                       and "journal" in dmesg_out.lower())
            issues.append(self._check(
                "ext4_journal_corruption", not corrupt,
                "The kernel logged EXT4 journal errors — the filesystem may be "
                "corrupting.",
                severity="critical"))

        # 74 undervoltage (vcgencmd get_throttled)
        thr = self._cmd_output("vcgencmd get_throttled")
        m = re.search(r"throttled=0x([0-9a-fA-F]+)", thr)
        val = int(m.group(1), 16) if m else None
        if val:
            issues.append(Issue(
                check_name="undervoltage",
                category=self.category_name,
                description="The Pi is (or has been) under-volted — use a "
                            "better supply/cable.",
                severity="critical" if (val & 0xF) else "warning",
                raw_detail=f"get_throttled=0x{val:x}",
            ))

        # 75 swap on SD card
        swap = self._cmd_output("swapon --show").strip()
        issues.append(self._check(
            "swap_on_sd", swap == "",
            "Swap is enabled on the SD card — this wears it out quickly.",
            severity="warning", auto_fixable=True,
            fix_description="Disable dphys-swapfile and swapoff."))

        # 76 timezone set (info) — flag the unlocalised UTC default
        tz = self._cmd_output(
            "timedatectl show -p Timezone --value").strip()
        issues.append(self._check(
            "timezone_set", tz not in ("Etc/UTC", "UTC"),
            "The timezone is still the UTC default — set the local timezone.",
            severity="info"))

        # 77 suspicious SD card manufacturer ID (0x00 / 0xAD)
        mid = self._cmd_output("cat /sys/block/mmcblk0/device/manfid").strip()
        mid_val = None
        try:
            mid_val = int(mid, 16)
        except ValueError:
            mid_val = None
        issues.append(self._check(
            "sd_card_suspicious", mid_val not in (0x00, 0xAD),
            "The SD card reports a suspect manufacturer ID often seen on "
            "counterfeit cards.",
            severity="warning"))

        # 79 Python too old (<3.9)
        pv = self._cmd_output("python3 --version")
        m = re.search(r"Python (\d+)\.(\d+)", pv)
        py_ok = True
        if m:
            py_ok = (int(m.group(1)), int(m.group(2))) >= (3, 9)
        issues.append(self._check(
            "python_version", py_ok,
            "Python is older than 3.9 — Reticulum needs a newer interpreter.",
            severity="warning"))

        # 80 pip too old (<21)
        pipv = self._cmd_output("pip3 --version")
        m = re.search(r"pip (\d+)", pipv)
        pip_ok = True if not m else int(m.group(1)) >= 21
        issues.append(self._check(
            "pip_version", pip_ok,
            "pip is older than version 21 and may fail to install wheels.",
            severity="warning", auto_fixable=True,
            fix_description="Upgrade pip."))

        return [i for i in issues if i is not None]

    # -- fixes -------------------------------------------------------------

    def _fix_handlers(self):
        return {
            "rnsd_unit_path": self._fix_unit_path,
            "swap_on_sd": self._fix_swap,
            "pip_version": self._fix_pip,
        }

    def _run_fix(self, issue, command, ok_msg):
        code, out, err = self._run_cmd(command)
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=ok_msg if ok else f"Failed: {err or out}",
                   raw_output=out)

    def _fix_unit_path(self, issue):
        which = self._cmd_output("which rnsd").strip() or "/usr/local/bin/rnsd"
        return self._run_fix(
            issue,
            f"sed -i 's#^ExecStart=.*rnsd.*#ExecStart={which}#' "
            "/etc/systemd/system/rnsd.service && systemctl daemon-reload",
            "Corrected the rnsd unit ExecStart path.")

    def _fix_swap(self, issue):
        return self._run_fix(
            issue,
            "dphys-swapfile swapoff && systemctl disable dphys-swapfile && "
            "swapoff -a",
            "Disabled swap.")

    def _fix_pip(self, issue):
        return self._run_fix(
            issue, "pip3 install --upgrade pip --break-system-packages",
            "Upgraded pip.")
