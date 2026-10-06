#!/usr/bin/env python3
"""Freeze THIS medic's installed Python packages into its wheelhouse, so a
clone installs exactly what this medic runs (workflows.parent_freeze).

    python3 scripts/freeze_parent_wheels.py [--all] [--dest DIR]

--all freezes the whole user site (platformio, esptool, …), not only the
manifest's closure; --dest writes into another folder (a rehearsal).
Run on the medic that makes clones, after any package change. No network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflows.parent_freeze import freeze  # noqa: E402
from workflows.wheelhouse import REQUIREMENTS, WHEELHOUSE  # noqa: E402


def main() -> int:
    dest = WHEELHOUSE
    if "--dest" in sys.argv:
        dest = sys.argv[sys.argv.index("--dest") + 1]
    ok, msg = freeze(dest, REQUIREMENTS, everything="--all" in sys.argv)
    print("[freeze]", msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
