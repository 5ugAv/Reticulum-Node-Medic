#!/usr/bin/env python3
"""Put the current health reporter on a Pi node that is already in the field.

    python3 scripts/push_reporter.py <host-or-ip> [--user pi]

Run FROM THE MEDIC ([[medic-orchestrates-testing]]): it copies the reporter
package over the same SSH road birth used, restarts the rnm-health service,
and PROVES each step by reading it back — the markers in the file, the trust
files' owner and mode, the service's own status. Nothing is claimed that was
not read back.

WHY THIS EXISTS AGAIN. It was a button on the node's page, then an automatic
push, and on 2026-09-29 it was deleted on the reasoning that every node would
be reflashed. The same day, the node-to-node link feature needed two new files
on ELSEWHERE — and skyfinger is on a roof. A rebirth writes an SD card; that is
the heaviest way there is to update two files. This is the light way, as a
tool the operator runs on purpose, not a screen that does SSH work behind
their back.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("host", help="the node's hostname or IP (e.g. elsewhere.local, 192.168.1.42)")
    ap.add_argument("--user", default="pi")
    ap.add_argument("--force", action="store_true",
                    help="push even if the node already carries the current reporter")
    args = ap.parse_args(argv)
    from transport.connection import SSHConnection
    from workflows.pi_reporter_push import push_health_reporter, reporter_is_current
    print(f"[push] connecting to {args.user}@{args.host}", flush=True)
    conn = SSHConnection(args.host, user=args.user)
    # ASK BEFORE PUSHING. A node that already carries the current reporter gets
    # a sentence, not a service restart — restarting rnm-health on a node in
    # the field for no reason is a beacon gap nobody asked for.
    if not args.force and reporter_is_current(conn):
        print("OK: the node already carries the current reporter — nothing to do "
              "(use --force to push anyway)")
        return 0
    ok, msg = push_health_reporter(conn, log=lambda m: print("[push]", m, flush=True))
    print(("OK: " if ok else "FAILED: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
