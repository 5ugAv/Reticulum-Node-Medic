#!/usr/bin/env python3
"""Recompile the RNode image BIRTH flashes onto a Heltec V4, from the fork.

    python3 scripts/rebuild_rnode_firmware.py          # on the medic

Birth reads ONE build directory (workflows.rnode_v4_rgb.BUILD_DIR). A `make`
in ~/RNode_Firmware writes somewhere else and changes nothing birth flashes;
on 2026-10-03 the NODE MEDIC strip was committed, `make`d twice, and a V4
was born with the old face. This runs the tool's own compile_command into
the right directory and then PROVES the result: the new binary carries the
bytes of the committed bm_def_lc header strip.
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflows.rnode_v4_rgb import (BUILD_BIN, FIRMWARE_DIR, compile_command,  # noqa: E402
                                    rgb_firmware_staleness)


def strip_signature(firmware_dir: str = FIRMWARE_DIR, rows: int = 6) -> bytes:
    src = open(os.path.join(os.path.expanduser(firmware_dir), "Graphics.h")).read()
    body = re.search(r"bm_def_lc\s*\[\]\s*PROGMEM\s*=\s*\{([^}]*)\}", src).group(1)
    return bytes(int(t, 16) for t in re.findall(r"0x[0-9a-fA-F]+", body))[:rows * 8]


def main() -> int:
    print("[rebuild] before:", rgb_firmware_staleness() or "image current")
    t0 = time.time()
    p = subprocess.run(["bash", "-lc", compile_command()], capture_output=True, text=True)
    tail = [l for l in p.stdout.splitlines() if "Sketch uses" in l or "error" in l.lower()]
    print("[rebuild] compile exit %d in %ds; %s" % (p.returncode, time.time() - t0,
                                                   "; ".join(tail[-2:]) or p.stderr[-300:]))
    if p.returncode != 0:
        return 1
    bp = os.path.expanduser(BUILD_BIN)
    blob = open(bp, "rb").read()
    sig = strip_signature()
    if sig not in blob:
        print("[rebuild] FAILED: the committed header strip is NOT in", bp)
        return 2
    print("[rebuild] OK: %s (%d bytes, %s) carries the committed strip; staleness now: %s"
          % (bp, len(blob), time.strftime("%H:%M", time.localtime(os.path.getmtime(bp))),
             rgb_firmware_staleness() or "current"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
