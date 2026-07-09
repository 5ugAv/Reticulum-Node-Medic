"""Reticulum software diagnostics (checks 1-11).

Verifies the Reticulum stack itself: the rnsd/lxmd services, serial-port
access, transport mode, the radio interface and the config file.
"""

from __future__ import annotations

import re
from typing import List

from diagnostics.base import DiagnosticCheck, Fix, Issue


class ReticulumSoftwareCheck(DiagnosticCheck):
    category_name = "Reticulum software"

    #: Reticulum config file on the node.
    config_path = "~/.reticulum/config"

    def run(self) -> List[Issue]:
        p = self.profile
        cfg = self.config_path
        port = p.radio.serial_port
        user = p.ssh_user
        issues = []

        # 1
        issues.append(self._check(
            "rnsd_running", self._service_is_active("rnsd"),
            "The Reticulum daemon (rnsd) is not running.",
            severity="critical", auto_fixable=True,
            fix_description="Start the rnsd service."))
        # 2
        issues.append(self._check(
            "rnsd_enabled", self._service_is_enabled("rnsd"),
            "rnsd is not set to start automatically on boot.",
            severity="warning", auto_fixable=True,
            fix_description="Enable the rnsd service at boot."))
        # 3
        issues.append(self._check(
            "lxmd_running", self._service_is_active("lxmd"),
            "The LXMF daemon (lxmd) is not running.",
            severity="critical", auto_fixable=True,
            fix_description="Start the lxmd service."))
        # 4
        issues.append(self._check(
            "lxmd_enabled", self._service_is_enabled("lxmd"),
            "lxmd is not set to start automatically on boot.",
            severity="warning", auto_fixable=True,
            fix_description="Enable the lxmd service at boot."))
        # 5
        groups = self._cmd_output(f"id -nG {user}")
        issues.append(self._check(
            "serial_port_permission", "dialout" in groups,
            f"User '{user}' is not in the 'dialout' group, so it cannot open "
            f"the radio serial port.",
            severity="critical", auto_fixable=True,
            fix_description=f"Add {user} to the dialout group."))
        # 6
        transport_on = self._run_cmd(
            f'grep "enable_transport = Yes" {cfg}')[0] == 0
        issues.append(self._check(
            "transport_mode_enabled", transport_on,
            "Transport mode is off — this node will not relay mesh traffic "
            "for other nodes.",
            severity="warning", auto_fixable=True,
            fix_description="Enable transport mode in the Reticulum config."))
        # 7 radio interface up. Use rnstatus --json and read the RNodeInterface's
        # own boolean "status" (verified on a live node) — "Up" anywhere in the
        # human output is meaningless when other interfaces are up but the radio
        # is down.
        iface = self._rnode_interface()
        radio_up = iface is not None and iface.get("status") is True
        issues.append(self._check(
            "radio_interface_up", radio_up,
            "The radio (RNode) interface is not up in Reticulum.",
            severity="critical"))
        # 8
        port_exists = self._run_cmd(f"test -c {port}")[0] == 0
        issues.append(self._check(
            "serial_port_exists", port_exists,
            f"The radio serial port {port} does not exist.",
            severity="critical"))
        # 9
        cfg_present = self._run_cmd(f"test -f {cfg}")[0] == 0
        issues.append(self._check(
            "config_present", cfg_present,
            "The Reticulum configuration file is missing.",
            severity="critical"))
        # 10
        rnode_configured = self._run_cmd(
            f'grep "RNodeInterface" {cfg}')[0] == 0
        issues.append(self._check(
            "rnode_interface_configured", rnode_configured,
            "No RNode radio interface is configured in Reticulum.",
            severity="critical"))
        # 11
        installed = self._run_cmd("which rnsd")[0] == 0
        issues.append(self._check(
            "reticulum_installed", installed,
            "Reticulum (rnsd) does not appear to be installed.",
            severity="critical"))

        # --- extended checks (50-53, 63, 78, 82-85) ----------------------

        # 50 radio-param warm-boot mismatch. rnsd writes its operational log to
        # ~/.reticulum/logfile, NOT the systemd journal (journalctl -u rnsd only
        # shows the "Started" line), so read the logfile or this always misses.
        rnsd_log = self._cmd_output("tail -n 300 ~/.reticulum/logfile")
        issues.append(self._check(
            "warm_boot_param_mismatch", "mismatch" not in rnsd_log.lower(),
            "rnsd logged a radio parameter mismatch after a warm boot "
            "(bandwidth / TX power / SF / radio state).",
            severity="warning", auto_fixable=True,
            fix_description="Restart rnsd to re-apply the radio parameters."))

        # 51 setfacl ACL permission on the serial port. A bare "rw" substring is
        # too loose (the owner line user::rw- is always present); check that rw
        # is actually reachable by our user — via a named ACL, the dialout group,
        # or ownership.
        # getfacl needs the `acl` package, which a stock Pi may not have. If it
        # produced nothing (not installed / error) don't claim there's no ACL
        # access — the dialout-group check (serial_port_permission) already
        # covers the common case, so a missing getfacl must NOT false-positive.
        acl = self._cmd_output(f"getfacl {port}")
        acl_ok = (not acl.strip()) or self._acl_grants_rw(acl, user)
        issues.append(self._check(
            "serial_acl", acl_ok,
            f"No read/write ACL grants {user} access to {port}.",
            severity="warning", auto_fixable=True,
            fix_description=f"Grant {user} rw on {port} via setfacl."))

        # 52 rnsd startup race (ExecStartPre / StartLimitBurst)
        rnsd_unit = self._cmd_output("systemctl cat rnsd")
        issues.append(self._check(
            "rnsd_startup_race",
            "ExecStartPre" in rnsd_unit or "StartLimitBurst" in rnsd_unit,
            "The rnsd unit has no startup delay — it may race the serial port "
            "on boot.",
            severity="warning", auto_fixable=True,
            fix_description="Add an ExecStartPre delay to the rnsd unit."))

        # 53 lxmd missing --service
        lxmd_unit = self._cmd_output("systemctl cat lxmd")
        issues.append(self._check(
            "lxmd_service_flag", "--service" in lxmd_unit,
            "The lxmd unit is missing the --service flag.",
            severity="warning", auto_fixable=True,
            fix_description="Add --service to the lxmd ExecStart line."))

        # 63 rnsd shared-instance failure cascade (lxmd journal)
        lxmd_journal = self._cmd_output("journalctl -u lxmd -n 300")
        issues.append(self._check(
            "shared_instance_cascade",
            "Reticulum will attempt to bring up" not in lxmd_journal,
            "lxmd could not attach to the shared rnsd instance and is trying "
            "to bring up its own — a failure cascade.",
            severity="warning"))

        # 78 config Windows (CRLF) line endings
        crlf = self._run_cmd(f"grep -c $'\\r' {cfg}")[1].strip()
        crlf_count = int(crlf) if crlf.isdigit() else 0
        issues.append(self._check(
            "config_line_endings", crlf_count == 0,
            "The config file has Windows (CRLF) line endings, which Reticulum "
            "mis-parses.",
            severity="warning", auto_fixable=True,
            fix_description="Strip carriage returns from the config."))

        # 82 shared-instance port 37428 conflict. Process names in `ss` need
        # root, so run privileged; and only flag a conflict when the owner is
        # actually identifiable and is not rnsd — otherwise we can't tell, so we
        # don't raise a false alarm.
        ss = self._cmd_output(self._priv("ss -tlnp | grep 37428"))
        identifiable = "pid=" in ss or "users:" in ss
        conflict = bool(ss.strip()) and identifiable and "rnsd" not in ss
        issues.append(self._check(
            "shared_instance_port_conflict", not conflict,
            "Another process is holding Reticulum's shared-instance port "
            "37428.",
            severity="critical"))

        # 83 identity file permissions 600
        idperm = self._cmd_output(
            "stat -c %a ~/.reticulum/storage/identity").strip()
        issues.append(self._check(
            "identity_permissions", idperm in ("", "600"),
            "The Reticulum identity file is not private (should be 600).",
            severity="warning", auto_fixable=True,
            fix_description="chmod 600 the identity file."))

        # 84 config directory permissions 700
        dperm = self._cmd_output("stat -c %a ~/.reticulum").strip()
        issues.append(self._check(
            "config_dir_permissions", dperm in ("", "700"),
            "The Reticulum config directory is not private (should be 700).",
            severity="warning", auto_fixable=True,
            fix_description="chmod 700 the config directory."))

        # 85 announce interval too aggressive (<120 s)
        ai = self._cmd_output(f"grep announce_interval {cfg}")
        interval = None
        if "=" in ai:
            tail = ai.split("=", 1)[1].strip()
            if tail.split() and tail.split()[0].isdigit():
                interval = int(tail.split()[0])
        issues.append(self._check(
            "announce_interval", interval is None or interval >= 120,
            f"The announce interval is very aggressive ({interval} s) — it "
            f"will congest the channel.",
            severity="warning"))

        return [i for i in issues if i is not None]

    @staticmethod
    def _acl_grants_rw(acl: str, user: str) -> bool:
        """True if the getfacl output grants *user* read/write, via a named
        user ACL, the dialout group, or ownership (not just any rw anywhere)."""
        lines = [ln.strip() for ln in acl.splitlines()]

        def entry_has_rw(prefixes):
            for ln in lines:
                for p in prefixes:
                    if ln.startswith(p) and "rw" in ln.rsplit(":", 1)[-1]:
                        return True
            return False

        if entry_has_rw([f"user:{user}:"]):        # explicit ACL for our user
            return True
        if entry_has_rw(["group:dialout:", "group::"]):  # via dialout group
            return True
        owner = ""
        for ln in lines:
            if ln.startswith("# owner:"):
                owner = ln.split(":", 1)[1].strip()
        if owner == user and entry_has_rw(["user::"]):
            return True
        return False

    # -- fixes -------------------------------------------------------------

    def _fix_handlers(self):
        return {
            "rnsd_running": lambda i: self._systemctl(i, "start", "rnsd"),
            "rnsd_enabled": lambda i: self._systemctl(i, "enable", "rnsd"),
            "lxmd_running": lambda i: self._systemctl(i, "start", "lxmd"),
            "lxmd_enabled": lambda i: self._systemctl(i, "enable", "lxmd"),
            "serial_port_permission": self._add_dialout,
            "transport_mode_enabled": self._enable_transport,
            "warm_boot_param_mismatch":
                lambda i: self._systemctl(i, "restart", "rnsd"),
            "serial_acl": self._fix_acl,
            "rnsd_startup_race": self._fix_startup_race,
            "lxmd_service_flag": self._fix_lxmd_service,
            "config_line_endings": self._fix_line_endings,
            "identity_permissions":
                lambda i: self._chmod(i, "600",
                                      "~/.reticulum/storage/identity"),
            "config_dir_permissions":
                lambda i: self._chmod(i, "700", "~/.reticulum"),
        }

    def _simple_fix(self, issue: Issue, command: str, ok_message: str) -> Fix:
        code, out, err = self._run_cmd(command)
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=(ok_message if ok else f"Failed: {err or out}"),
                   raw_output=out)

    def _fix_acl(self, issue: Issue) -> Fix:
        port = self.profile.radio.serial_port
        user = self.profile.ssh_user
        return self._simple_fix(
            issue, f"sudo setfacl -m u:{user}:rw {port}",
            f"Granted {user} rw on {port}.")

    def _fix_startup_race(self, issue: Issue) -> Fix:
        return self._simple_fix(
            issue,
            "mkdir -p /etc/systemd/system/rnsd.service.d && "
            "printf '[Service]\\nExecStartPre=/bin/sleep 5\\n' > "
            "/etc/systemd/system/rnsd.service.d/delay.conf && "
            "systemctl daemon-reload",
            "Added a 5s startup delay to rnsd.")

    def _fix_lxmd_service(self, issue: Issue) -> Fix:
        return self._simple_fix(
            issue,
            "sed -i 's#ExecStart=\\(.*lxmd\\)$#ExecStart=\\1 --service#' "
            "/etc/systemd/system/lxmd.service && systemctl daemon-reload",
            "Added --service to lxmd.")

    def _fix_line_endings(self, issue: Issue) -> Fix:
        return self._simple_fix(
            issue, f"sed -i 's/\\r//' {self.config_path}",
            "Stripped carriage returns from the config.")

    def _chmod(self, issue: Issue, mode: str, path: str) -> Fix:
        return self._simple_fix(
            issue, f"chmod {mode} {path}", f"Set {path} to {mode}.")

    _PAST_TENSE = {"start": "Started", "enable": "Enabled",
                   "restart": "Restarted", "stop": "Stopped"}

    def _systemctl(self, issue: Issue, action: str, service: str) -> Fix:
        code, out, err = self._run_cmd(f"systemctl {action} {service}")
        ok = code == 0
        past = self._PAST_TENSE.get(action, f"{action}ed")
        return Fix(
            issue=issue,
            success=ok,
            message=(f"{past} {service}" if ok
                     else f"Could not {action} {service}: {err or out}"),
            raw_output=out,
        )

    def _add_dialout(self, issue: Issue) -> Fix:
        user = self.profile.ssh_user
        code, out, err = self._run_cmd(f"sudo usermod -aG dialout {user}")
        ok = code == 0
        return Fix(
            issue=issue,
            success=ok,
            message=(f"Added {user} to dialout (re-login required)" if ok
                     else f"Could not modify groups: {err or out}"),
            raw_output=out,
        )

    def _enable_transport(self, issue: Issue) -> Fix:
        code, out, err = self._run_cmd(
            f"sed -i 's/enable_transport = No/enable_transport = Yes/' "
            f"{self.config_path}")
        ok = code == 0
        return Fix(
            issue=issue,
            success=ok,
            message=("Enabled transport mode (restart rnsd to apply)" if ok
                     else f"Could not edit config: {err or out}"),
            raw_output=out,
        )
