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
        # Is the radio interface up? When it's DOWN, the mesh checks below are all
        # downstream cascade symptoms (no peers, no announces, empty path table),
        # so gate them on this. The DOWN radio itself is reported ONCE by
        # reticulum_software.radio_interface_up (which owns it, with the rnsd
        # journal cause) — this module no longer double-reports it.
        iface_up = bool(iface and iface.get("status"))
        issues = []

        # 36 peers heard — a destination learned over a real (non-local)
        # interface, not just this node's own local destinations.
        remote = [p for p in paths
                  if not str(p.get("interface", "")).startswith("LocalInterface")]
        issues.append(self._check(
            "peers_heard", (not iface_up) or len(remote) > 0,
            "No other mesh nodes have been heard from.",
            severity="warning"))

        # 37 announces sending. Primary signal is the rnstatus field
        # outgoing_announce_frequency (verified present in real rnstatus --json)
        # — a node originating/forwarding announces reports > 0 on an interface.
        # Fall back to the rnsd JOURNAL, not ~/.reticulum/logfile. Verified on the
        # live Pi: rnsd-under-systemd writes NO logfile (the path does not exist)
        # and logs to the journal (loglevel 4) — so the old `tail ~/.reticulum/
        # logfile` always read nothing. The rnstatus field is the primary signal;
        # this only backstops it.
        announcing = any(
            float(i.get("outgoing_announce_frequency") or 0) > 0
            for i in interfaces)
        if iface_up and not announcing:
            log = self._cmd_output(
                "journalctl -u rnsd -n 500 --no-pager").lower()
            announcing = "announce" in log
        issues.append(self._check(
            "announces_sending", (not iface_up) or announcing,
            "This node is not sending announces onto the mesh.",
            severity="warning"))

        # 38 path table populated — any known destinations at all.
        issues.append(self._check(
            "path_table_populated", (not iface_up) or len(paths) > 0,
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

        # 41 L2 — actively reach a KNOWN peer over the mesh. rnping needs a real
        # destination hash; the old "mesh-test" placeholder never resolved, so
        # this always false-failed. A real L2 fault only exists when the radio is
        # UP and a known peer won't reply — a DOWN radio is already reported by L1
        # (verified live: without this gate L2 double-reported a down interface),
        # and "no peers" by peers_heard.
        peer_hash = remote[0].get("hash") if remote else None
        if iface_up and peer_hash:
            reply = "reply" in self._cmd_output(f"rnping {peer_hash}").lower()
        else:
            reply = True
        issues.append(self._check(
            "mesh_ping_l2", reply,
            f"Level 2: no reply from known mesh peer {peer_hash}.",
            severity="critical"))

        # 42 L3 — announces are actually flowing IN. A meshed node hears peers'
        # announces (rnstatus incoming_announce_frequency), or at least has learnt
        # paths. The old `rnprobe mesh-test` probed a placeholder that never
        # existed. Only flag when the radio is UP but nothing is being heard
        # (a down radio is already reported by L1 — don't double-report).
        heard = (any(float(i.get("incoming_announce_frequency") or 0) > 0
                     for i in interfaces) or len(paths) > 0)
        issues.append(self._check(
            "announce_heard_l3", (not iface_up) or heard,
            "Level 3: the radio is up but no announces are being heard from the "
            "mesh.",
            severity="warning"))

        # 43 Reticulum identity present. A transport node stores it at
        # storage/transport_identity, a client at storage/identity — check BOTH.
        # Verified on the live Pi: only transport_identity exists, so keying on
        # `identity` alone false-reported "no identity" on a node that has one.
        has_identity = (
            self._run_cmd("test -f ~/.reticulum/storage/identity")[0] == 0
            or self._run_cmd(
                "test -f ~/.reticulum/storage/transport_identity")[0] == 0)
        issues.append(self._check(
            "reticulum_identity", has_identity,
            "The node has no Reticulum identity file.",
            severity="critical"))

        return [i for i in issues if i is not None]
