#!/usr/bin/env python3
"""Ping a node for its health from a shell — the same two-phase request
the medic's "Ping node now" makes, runnable when nobody is at the glass
(docs/HEALTH_REPLY_UNICAST.md, 2026-09-22).

    python3 scripts/rnm_health_ping.py <rtnode.health destination hash>

Attaches to the running rnsd, makes a throwaway reply identity, announces
it (so the node can encrypt to it), sends 0x04 | reply | nonce, and waits
W1 for a signed unicast reply; then 0x01 and waits W2 for an announce.
Prints what happened and exits 0 on an answer, 1 on silence, 2 on a
setup failure. Nothing is written to the medic's registry: this is a
bench instrument, not the medic.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

# Run from anywhere: the repo root, not scripts/, is the import root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor import health_beacon
from monitor.formatting import format_age
from monitor.health_reply import (announce_wait_s, build_fallback_request,
                                  build_request_to, load_or_create_identity,
                                  unicast_wait_s, verify_reply,
                                  PING_TOOL_IDENTITY_PATH, REPLY_APP, REPLY_ASPECTS)


def rebooted_since(prev_uptime_s, now_uptime_s, jitter_s: int = 30) -> bool:
    """True when *now_uptime_s* is not just a fresh reading of the same run —
    the node RESTARTED between the two pings (lost power and came back).

    No sensor reports a Pi node's battery (2026-09-24: checked, none of the
    chain from cells to charger to Pi carries one). Repeated pings give one
    honest fact instead: uptime always counts UP within a run, so a drop of
    more than a few seconds of jitter means the clock started over — a
    reboot, whatever caused it. *prev_uptime_s* of None (first ping, or a
    beacon that could not be decoded) is never called a reboot."""
    if prev_uptime_s is None or now_uptime_s is None:
        return False
    return now_uptime_s < prev_uptime_s - jitter_s


#: This tool's own memory of the last uptime it read from each node — the
#: no-sensor battery watch (2026-09-24). Local to the BENCH TOOL, not the
#: medic's registry (this script writes nothing there, on purpose).
PING_STATE_PATH = os.path.expanduser("~/.reticulum-node-medic/health_ping_state.json")


def load_last_uptime(node_dest_hex: str, path: str = PING_STATE_PATH):
    """The uptime this tool last read from *node_dest_hex*, or None (never
    pinged, or the file is missing/corrupt — never raises)."""
    try:
        with open(path) as f:
            data = json.load(f)
        v = data.get(node_dest_hex)
        return int(v) if v is not None else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_last_uptime(node_dest_hex: str, uptime_s: int, path: str = PING_STATE_PATH) -> None:
    try:
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data[node_dest_hex] = int(uptime_s)
        from monitor.atomic_json import write_json
        write_json(path, data)
    except Exception:                                              # noqa: BLE001
        pass                     # the ping's own answer is what matters; state is a bonus


def describe_beacon(raw: bytes) -> str:
    """One line from the reply beacon's own bytes — uptime is the field that
    matters for the no-sensor battery watch: a small number means the node
    restarted since it was last seen running."""
    try:
        b = health_beacon.decode(raw)
    except Exception as e:                                          # noqa: BLE001
        return f"beacon {len(raw)} bytes (could not decode: {e})"
    return f"beacon {len(raw)} bytes, uptime {format_age(b.uptime_s / 3600.0)}"


def _note_uptime(node_dest_hex: str, raw_beacon: bytes) -> None:
    """The no-sensor battery watch: compare this reply's uptime against the
    last one this tool saw from the same node, and say plainly if it
    rebooted in between (2026-09-24 — repeated pings are the one honest
    signal available; nothing in a Pi node's chain reports its battery)."""
    try:
        uptime_s = health_beacon.decode(raw_beacon).uptime_s
    except Exception:                                              # noqa: BLE001
        return
    prev = load_last_uptime(node_dest_hex)
    if rebooted_since(prev, uptime_s):
        print(f"NOTE: uptime fell since the last ping ({format_age(prev / 3600.0)} -> "
              f"{format_age(uptime_s / 3600.0)}) — the node restarted in between "
              "(power loss and recovery, or a manual reboot).")
    save_last_uptime(node_dest_hex, uptime_s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dest", help="the node's rtnode.health destination hash (32 hex)")
    ap.add_argument("--hops", type=int, default=1, help="path length, for the wait windows")
    ap.add_argument("--path-wait", type=float, default=15.0)
    args = ap.parse_args(argv)
    try:
        dest_hash = bytes.fromhex(args.dest)
        assert len(dest_hash) == 16
    except Exception:                                                  # noqa: BLE001
        print("dest must be 32 hex characters", file=sys.stderr)
        return 2
    try:
        import RNS
        RNS.Reticulum()
    except Exception as e:                                             # noqa: BLE001
        print(f"could not attach to rnsd: {e}", file=sys.stderr)
        return 2

    ident = RNS.Identity.recall(dest_hash)
    if ident is None:
        print("the node's identity is not known here (no announce heard yet) — cannot request")
        return 2
    node_dest = RNS.Destination(ident, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                "rtnode", "health")

    got = {"unicast": None, "announce": None, "announce_at": None, "unicast_at": None}
    ev = threading.Event()          # set only by a UNICAST reply
    ann = threading.Event()         # set by an announce with a beacon
    # ONE persistent identity for this tool — a fresh one per run announced a
    # new destination every time, and the medic listed each as a neighbour.
    my_ident = load_or_create_identity(RNS, PING_TOOL_IDENTITY_PATH)
    reply_dest = RNS.Destination(my_ident, RNS.Destination.IN, RNS.Destination.SINGLE,
                                 REPLY_APP, *REPLY_ASPECTS)

    def on_reply(data, packet):
        r = verify_reply(bytes(data), recall=RNS.Identity.recall)
        if r is not None and r[0] == node_dest.hash:
            got["unicast"] = r
            got["unicast_at"] = time.time()
            ev.set()
    reply_dest.set_packet_callback(on_reply)

    class _Ann:
        aspect_filter = "rtnode.health"
        def received_announce(self, destination_hash, announced_identity, app_data):
            if destination_hash == node_dest.hash and app_data:
                # An announce is NOT taken as the answer by itself: a path
                # request makes a node re-announce, and that lands within
                # a second of the request — before any reply could. Note it;
                # the unicast is what proves the new path.
                got["announce"] = bytes(app_data)
                got["announce_at"] = got["announce_at"] or time.time()
                ann.set()
    RNS.Transport.register_announce_handler(_Ann())

    reply_dest.announce()
    print(f"reply destination {reply_dest.hash.hex()[:8]} announced; warming path to {args.dest[:8]}…")
    t0 = time.time()
    while not RNS.Transport.has_path(dest_hash) and time.time() - t0 < args.path_wait:
        RNS.Transport.request_path(dest_hash)
        time.sleep(1.0)
    if not RNS.Transport.has_path(dest_hash):
        print("no path to the node from this stack — request not sent")
        return 1

    sent = time.time()
    req = build_request_to(bytes(reply_dest.hash))
    RNS.Packet(node_dest, req).send()
    w1 = unicast_wait_s(args.hops)
    print(f"0x04 sent; waiting up to {w1:.0f} s for a signed unicast reply…")
    if ev.wait(w1) and got["unicast"]:
        _, nonce, beacon = got["unicast"]
        extra = (f"; an announce also arrived at +{got['announce_at'] - sent:.1f} s"
                 if got["announce_at"] else "")
        print(f"ANSWERED by unicast in {got['unicast_at'] - sent:.1f} s: nonce echoed={nonce == req[-8:]}, "
              f"{describe_beacon(beacon)}{extra}")
        _note_uptime(args.dest, beacon)
        return 0
    if got["announce"]:
        print(f"no unicast reply in {w1:.0f} s; an announce arrived at +{got['announce_at'] - sent:.1f} s "
              "(old reporter/firmware, no path back, or a path-request re-announce)")
        return 0
    print("no unicast reply; sending 0x01 (announce request)…")
    RNS.Packet(node_dest, build_fallback_request()).send()
    w2 = announce_wait_s(args.hops)
    ann.clear()
    if ev.wait(0) or ann.wait(w2):
        how = "unicast (late)" if got["unicast"] else "announce"
        print(f"ANSWERED by {how} in {time.time() - sent:.1f} s")
        return 0
    print(f"SILENT after {time.time() - sent:.0f} s (W1 {w1:.0f} s + W2 {w2:.0f} s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
