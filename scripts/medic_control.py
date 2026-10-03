#!/usr/bin/env python3
"""Talk to the running medic's control socket (ui/remote.py).

    python3 scripts/medic_control.py list
    python3 scripts/medic_control.py open vitals
    python3 scripts/medic_control.py node a1b2c3d4
    python3 scripts/medic_control.py home

Run ON the medic (or over ssh). Prints the app's reply; exit 0 on "ok".
"""
import socket
import sys

SOCKET_PATH = "/tmp/nodemedic-control.sock"


def main(argv):
    if not argv:
        print(__doc__); return 2
    line = " ".join(argv)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(10.0)
    try:
        s.connect(SOCKET_PATH)
        s.sendall((line + "\n").encode("utf-8"))
        reply = s.recv(4096).decode("utf-8", "replace").strip()
    except OSError as exc:
        print(f"err cannot reach the medic's control socket: {exc}"); return 1
    finally:
        s.close()
    print(reply)
    return 0 if reply.startswith("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
