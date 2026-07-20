#!/usr/bin/env python3
"""Report whether an RNode's stored firmware hash matches the firmware actually
running on it — i.e. whether the board is in the "firmware corrupt" state.

An RNode can have a VALID EEPROM + a validated device signature yet still show
"firmware corrupt" on its display: that happens when the firmware hash stamped in
the EEPROM (CMD_HASHES 0x01) differs from the hash the firmware computes for
itself at boot (CMD_HASHES 0x02). ``rnodeconf --info`` does NOT surface this, so
the medic's PROBE reads both hashes directly via rnodeconf's own RNode class and
compares them.

Usage:  python3 fw_hash_probe.py /dev/ttyACM1
Prints exactly one line:
    FWHASH:MATCH               stored == computed (firmware OK)
    FWHASH:MISMATCH <s> <c>    stored != computed ("firmware corrupt")
    FWHASH:UNKNOWN <reason>    couldn't read both hashes (don't flag a fault)
"""

import sys
import threading
import time


def main() -> int:
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM1"
    try:
        from RNS.Utilities.rnodeconf import RNode, rnode_open_serial
    except Exception as exc:                       # pragma: no cover
        print(f"FWHASH:UNKNOWN import-failed:{exc}")
        return 0
    try:
        rnode = RNode(rnode_open_serial(port))
        threading.Thread(target=rnode.readLoop, daemon=True).start()
        try:
            rnode.device_probe()
        except Exception:
            pass                                   # probe raises on non-RNodes
        time.sleep(2)
        stored = rnode.firmware_hash
        computed = rnode.firmware_hash_target
    except Exception as exc:
        print(f"FWHASH:UNKNOWN read-failed:{exc}")
        return 0
    if not stored or not computed:
        print("FWHASH:UNKNOWN no-hashes-reported")
        return 0
    if stored == computed:
        print("FWHASH:MATCH")
    else:
        print(f"FWHASH:MISMATCH {stored.hex()} {computed.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
