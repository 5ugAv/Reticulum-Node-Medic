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
import sys
import threading
import time

from monitor.health_reply import (announce_wait_s, build_fallback_request,
                                  build_request_to, unicast_wait_s, verify_reply,
                                  REPLY_APP, REPLY_ASPECTS)


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

    got = {"unicast": None, "announce": None}
    ev = threading.Event()
    my_ident = RNS.Identity()
    reply_dest = RNS.Destination(my_ident, RNS.Destination.IN, RNS.Destination.SINGLE,
                                 REPLY_APP, *REPLY_ASPECTS)

    def on_reply(data, packet):
        r = verify_reply(bytes(data), recall=RNS.Identity.recall)
        if r is not None and r[0] == node_dest.hash:
            got["unicast"] = r
            ev.set()
    reply_dest.set_packet_callback(on_reply)

    class _Ann:
        aspect_filter = "rtnode.health"
        def received_announce(self, destination_hash, announced_identity, app_data):
            if destination_hash == node_dest.hash and app_data:
                got["announce"] = bytes(app_data)
                ev.set()
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
        print(f"ANSWERED by unicast in {time.time() - sent:.1f} s: nonce echoed={nonce == req[-8:]}, "
              f"beacon {len(beacon)} bytes")
        return 0
    if got["announce"]:
        print(f"ANSWERED by announce in {time.time() - sent:.1f} s (old firmware or no path back)")
        return 0
    print("no unicast reply; sending 0x01 (announce request)…")
    RNS.Packet(node_dest, build_fallback_request()).send()
    w2 = announce_wait_s(args.hops)
    if ev.wait(w2) and (got["announce"] or got["unicast"]):
        how = "unicast (late)" if got["unicast"] else "announce"
        print(f"ANSWERED by {how} in {time.time() - sent:.1f} s")
        return 0
    print(f"SILENT after {time.time() - sent:.0f} s (W1 {w1:.0f} s + W2 {w2:.0f} s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
