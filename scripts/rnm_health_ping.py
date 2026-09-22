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
import os
import sys
import threading
import time

# Run from anywhere: the repo root, not scripts/, is the import root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.health_reply import (announce_wait_s, build_fallback_request,
                                  build_request_to, load_or_create_identity,
                                  unicast_wait_s, verify_reply,
                                  PING_TOOL_IDENTITY_PATH, REPLY_APP, REPLY_ASPECTS)


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
              f"beacon {len(beacon)} bytes{extra}")
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
