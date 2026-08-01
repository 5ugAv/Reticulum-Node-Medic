"""Fix the T-Beam Supreme's boot freeze: power the display bus BEFORE using it.

    python3 apply_tbeam_supreme_pmu_fix.py path/to/RNode_Firmware.ino

THE BUG (found live 2026-08-01, on a brand-new T-Beam Supreme). Stock
``RNode_Firmware.ino`` runs ``display_init()`` and only *afterwards*
``init_pmu()``. On most boards that is harmless — the OLED is wired straight to
3V3. On the T-Beam Supreme it is not: the screen (with the IMU, magnetometer
and BME280) hangs off the PMU's **ALDO1** rail, which is switched OFF until
``init_pmu()`` turns it on. LilyGO's own hardware notes are explicit that
touching that I²C bus before its rail is powered "will fail **or freeze**".

So the firmware freezes inside ``display_init()`` in ``setup()``, before it
ever reaches its serial loop. Symptoms, all of which we saw:

  * the OLED stays dark,
  * the board never answers ``rnodeconf`` ("Serial port opened, but RNode did
    not respond"), so autoinstall can't provision it,
  * yet the chip is perfectly healthy — esptool talks to the ROM bootloader
    and every flashed segment verifies.

THE FIX. Bring the PMU up first, for this board only, by inserting an early
``init_pmu()`` ahead of the display block. ``init_pmu()`` is idempotent (it
reuses an existing ``PMU`` object and simply re-applies the rail voltages), so
the original later call is left exactly where it is and every other board is
untouched.

Idempotent: re-running detects the marker and does nothing.
"""

from __future__ import annotations

import argparse
import sys

MARKER = "RNM: PMU BEFORE DISPLAY"

#: Insert ahead of the display block — matched on the firmware's own comment.
ANCHOR = "  #if HAS_DISPLAY"

PATCH = '''  // %s — the T-Beam Supreme's OLED (and its IMU/BME280) sit on the PMU's
  // ALDO1 rail, which is OFF until init_pmu() switches it on. Touching that
  // I2C bus first makes the transaction hang, freezing setup() before the
  // serial loop ever starts: dark screen, no rnodeconf response, unflashable
  // as an RNode (diagnosed live 2026-08-01). init_pmu() is idempotent, so the
  // original call below stays put and other boards are unaffected.
  #if BOARD_MODEL == BOARD_TBEAM_S_V1
    pmu_ready = init_pmu();
  #endif

''' % MARKER


def apply(ino_path: str) -> str:
    with open(ino_path) as f:
        src = f.read()
    if MARKER in src:
        return "already patched"
    if ANCHOR not in src:
        raise ValueError(f"anchor missing: {ANCHOR!r}")
    # the FIRST HAS_DISPLAY block in setup() is the one that inits the display
    src = src.replace(ANCHOR, PATCH + ANCHOR, 1)
    with open(ino_path, "w") as f:
        f.write(src)
    return "patched: PMU now initialised before the display bus"


def is_patched(contents: str) -> bool:
    return MARKER in contents


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ino")
    args = ap.parse_args()
    try:
        print(apply(args.ino))
    except (OSError, ValueError) as e:
        print(f"tbeam-supreme PMU fix failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
