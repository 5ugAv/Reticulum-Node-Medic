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
        # Robust JSON (verified against RNS 1.3.7 on a live node). rnpath -t
        # --json is a list of {hash, via, hops, expires, interface}.
        paths = self._rnpath_json()
        iface = self._rnode_interface()
        issues = []

        # 36 peers heard — a destination learned over a real (non-local)
        # interface, not just this node's own local destinations.
        remote = [p for p in paths
                  if not str(p.get("interface", "")).startswith("LocalInterface")]
        issues.append(self._check(
            "peers_heard", len(remote) > 0,
            "No other mesh nodes have been heard from.",
            severity="warning"))

        # 37 announces sending
        journal = self._cmd_output("journalctl -u rnsd -n 200").lower()
        issues.append(self._check(
            "announces_sending", "announce" in journal,
            "This node is not sending announces onto the mesh.",
            severity="warning"))

        # 38 path table populated — any known destinations at all.
        issues.append(self._check(
            "path_table_populated", len(paths) > 0,
            "The path table is empty — no destinations are known.",
            severity="warning"))

        # 39 channel congestion — RNodeInterface channel_load_short is a 0.0-1.0
        # fraction (verified: 0.07 == 7%), so the 70% threshold is 0.70.
        load = float(iface.get("channel_load_short", 0.0)) if iface else 0.0
        issues.append(self._check(
            "channel_congestion", load < 0.70,
            f"The LoRa channel is congested ({load * 100:.0f}% airtime).",
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
