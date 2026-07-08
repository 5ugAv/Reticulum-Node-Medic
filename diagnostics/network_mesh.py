"""Network & mesh diagnostics (checks 36-43).

Peer reachability, announces, the path table, channel congestion, the
three-level ping (L1 serial loopback -> L2 mesh ping -> L3 announce heard by
the tool) and the node's Reticulum identity.
"""

from __future__ import annotations

import re
from typing import List

from diagnostics.base import DiagnosticCheck, Issue


class NetworkMeshCheck(DiagnosticCheck):
    category_name = "Network & mesh"

    def run(self) -> List[Issue]:
        port = self.profile.radio.serial_port
        rnstatus = self._cmd_output("rnstatus")
        issues = []

        # 36 peers heard
        issues.append(self._check(
            "peers_heard", bool(self._cmd_output("rnpath -t").strip()),
            "No other mesh nodes have been heard from.",
            severity="warning"))

        # 37 announces sending
        journal = self._cmd_output("journalctl -u rnsd -n 200").lower()
        issues.append(self._check(
            "announces_sending", "announce" in journal,
            "This node is not sending announces onto the mesh.",
            severity="warning"))

        # 38 path table populated
        m = re.search(r"(\d+)\s+paths known", rnstatus)
        paths = int(m.group(1)) if m else 0
        issues.append(self._check(
            "path_table_populated", paths > 0,
            "The path table is empty — no destinations are known.",
            severity="warning"))

        # 39 channel congestion
        m = re.search(r"Channel load:\s*(\d+)%", rnstatus)
        load = int(m.group(1)) if m else 0
        issues.append(self._check(
            "channel_congestion", load < 70,
            f"The LoRa channel is congested ({load}% airtime).",
            severity="warning"))

        # 40 L1 serial loopback
        issues.append(self._check(
            "loopback_l1", self._run_cmd(f"rnodeconf {port} --loop")[0] == 0,
            "Level 1 test failed: the radio did not pass a serial loopback.",
            severity="critical"))

        # 41 L2 mesh ping
        ping = self._cmd_output("rnping mesh-test").lower()
        issues.append(self._check(
            "mesh_ping_l2", "reply" in ping,
            "Level 2 test failed: no reply to a Reticulum mesh ping.",
            severity="critical"))

        # 42 L3 announce heard by the tool
        issues.append(self._check(
            "announce_heard_l3", self._run_cmd("rnprobe mesh-test")[0] == 0,
            "Level 3 test failed: the tool did not hear this node's announce.",
            severity="warning"))

        # 43 Reticulum identity present
        issues.append(self._check(
            "reticulum_identity",
            self._run_cmd("test -f ~/.reticulum/storage/identity")[0] == 0,
            "The node has no Reticulum identity file.",
            severity="critical"))

        return [i for i in issues if i is not None]
