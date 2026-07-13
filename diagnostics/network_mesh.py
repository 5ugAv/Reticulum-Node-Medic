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
        # --json is a list of {hash, via, hops, expires, interface}; rnstatus
        # --json is {"interfaces": [...], ...}. Fetch rnstatus once.
        paths = self._rnpath_json()
        interfaces = self._rnstatus_json().get("interfaces", [])
        iface = next((i for i in interfaces
                      if i.get("type") == "RNodeInterface"), None)
        issues = []

        # 36 peers heard — a destination learned over a real (non-local)
        # interface, not just this node's own local destinations.
        remote = [p for p in paths
                  if not str(p.get("interface", "")).startswith("LocalInterface")]
        issues.append(self._check(
            "peers_heard", len(remote) > 0,
            "No other mesh nodes have been heard from.",
            severity="warning"))

        # 37 announces sending. Primary signal is the rnstatus field
        # outgoing_announce_frequency (verified present in real rnstatus --json)
        # — a node originating/forwarding announces reports > 0 on an interface.
        # Fall back to the rnsd logfile (~/.reticulum/logfile, format
        # "[YYYY-MM-DD HH:MM:SS] [Level] Sending announce ..."), NOT journalctl:
        # the rnsd systemd unit only journals its "Started" line, so scraping
        # journalctl for announce activity always misses it (false negative).
        announcing = any(
            float(i.get("outgoing_announce_frequency") or 0) > 0
            for i in interfaces)
        if not announcing:
            log = self._cmd_output("tail -n 500 ~/.reticulum/logfile").lower()
            announcing = "announce" in log
        issues.append(self._check(
            "announces_sending", announcing,
            "This node is not sending announces onto the mesh.",
            severity="warning"))

        # 38 path table populated — any known destinations at all.
        issues.append(self._check(
            "path_table_populated", len(paths) > 0,
            "The path table is empty — no destinations are known.",
            severity="warning"))

        # 39 channel congestion — RNodeInterface channel_load_short is a PERCENT
        # (0-100), NOT a 0.0-1.0 fraction (verified on a live node: human rnstatus
        # prints "Ch. Load : 0.14%" while the JSON value is 0.14; a busy node read
        # 18.66). So the 70% line is 70.0 and the value is already the percentage
        # (the old `load < 0.70` + `load*100` read a healthy node as "675%").
        load = float(iface.get("channel_load_short", 0.0)) if iface else 0.0
        issues.append(self._check(
            "channel_congestion", load < 70.0,
            f"The LoRa channel is congested ({load:.0f}% airtime).",
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
