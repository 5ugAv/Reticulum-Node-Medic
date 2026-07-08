"""Client connectivity diagnostics (checks 44-49).

Confirms the node's client-facing surfaces: the Reticulum TCP interface, LXMF
delivery, and — where the profile says they exist — MeshChat, Sideband/Columba
storage, and a Meshtastic bridge.
"""

from __future__ import annotations

from typing import List

from diagnostics.base import DiagnosticCheck, Fix, Issue

#: Default Reticulum TCP interface listen port.
TCP_PORT = 4242
#: Default MeshChat web/TCP port.
MESHCHAT_PORT = 8000


class ClientConnectivityCheck(DiagnosticCheck):
    category_name = "Client connectivity"

    def run(self) -> List[Issue]:
        p = self.profile
        issues = []

        # 44 TCP interface listening (always)
        issues.append(self._check(
            "tcp_interface_listening",
            self._run_cmd(f"ss -tlnp | grep :{TCP_PORT}")[0] == 0,
            "The Reticulum TCP interface is not listening for client "
            "connections.",
            severity="warning"))

        # 45 LXMF delivery running (always)
        issues.append(self._check(
            "lxmf_delivery_running",
            self._run_cmd("pgrep -f lxmd")[0] == 0,
            "LXMF message delivery is not running.",
            severity="warning"))

        # 46 MeshChat TCP (if MeshChat client present)
        if p.has_meshchat_client:
            issues.append(self._check(
                "meshchat_tcp",
                self._run_cmd(f"ss -tlnp | grep :{MESHCHAT_PORT}")[0] == 0,
                "MeshChat is not reachable on its TCP port.",
                severity="warning"))

        # 47 LXMF storage dir (if Sideband or Columba present)
        if p.has_sideband_client or p.has_columba_client:
            issues.append(self._check(
                "lxmf_storage_dir",
                self._run_cmd("test -d ~/.reticulum/storage/lxmf")[0] == 0,
                "The LXMF storage directory is missing — clients cannot "
                "store messages.",
                severity="warning"))

        # 48 Meshtastic bridge running (if Meshtastic client present)
        if p.has_meshtastic_client:
            issues.append(self._check(
                "meshtastic_bridge_running",
                self._service_is_active("meshtastic-bridge"),
                "The Meshtastic bridge service is not running.",
                severity="warning"))

        # 49 Meshtastic board connected (if bridge hardware present)
        if p.has_meshtastic_bridge:
            issues.append(self._check(
                "meshtastic_board_connected",
                self._run_cmd("test -c /dev/ttyACM0")[0] == 0,
                "No Meshtastic board is connected to the bridge.",
                severity="warning"))

        # --- extended checks (54-56, 81) ---------------------------------
        journal = self._cmd_output("journalctl -u lxmd -n 200").lower()

        # 54 lxmd message store full
        issues.append(self._check(
            "lxmd_store_full", "store full" not in journal,
            "The LXMF message store is full — new messages are being dropped.",
            severity="warning"))

        # 55 lxmd statistics timeout
        issues.append(self._check(
            "lxmd_statistics_timeout", "statistics timeout" not in journal,
            "lxmd statistics requests are timing out — the propagation node "
            "may be overloaded.",
            severity="warning"))

        # 56 lxmd peer limit
        issues.append(self._check(
            "lxmd_peer_limit", "peer limit" not in journal,
            "lxmd has hit its peer limit — some peers are being refused.",
            severity="warning"))

        # 81 lxmd unit missing After=/Wants=rnsd.service
        unit = self._cmd_output("systemctl cat lxmd")
        issues.append(self._check(
            "lxmd_after_rnsd", "rnsd.service" in unit,
            "The lxmd service does not depend on rnsd, so it can start before "
            "the mesh is up.",
            severity="warning", auto_fixable=True,
            fix_description="Add After=/Wants=rnsd.service to the lxmd unit."))

        return [i for i in issues if i is not None]

    # -- fixes -------------------------------------------------------------

    def _fix_handlers(self):
        return {"lxmd_after_rnsd": self._fix_lxmd_after}

    def _fix_lxmd_after(self, issue: Issue) -> Fix:
        cmd = (
            "mkdir -p /etc/systemd/system/lxmd.service.d && "
            "printf '[Unit]\\nAfter=rnsd.service\\nWants=rnsd.service\\n' > "
            "/etc/systemd/system/lxmd.service.d/after.conf && "
            "systemctl daemon-reload"
        )
        code, out, err = self._run_cmd(cmd)
        ok = code == 0
        return Fix(issue=issue, success=ok,
                   message=("Added rnsd dependency to lxmd." if ok
                            else f"Failed: {err or out}"),
                   raw_output=out)
