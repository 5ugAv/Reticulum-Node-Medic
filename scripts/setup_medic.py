#!/usr/bin/env python3
"""Set up this Raspberry Pi 5 as a Node Medic, from GitHub alone.

    python3 ~/reticulum-tool/scripts/setup_medic.py            # set up, or carry on
    python3 ~/reticulum-tool/scripts/setup_medic.py --check    # only say what is missing
    python3 ~/reticulum-tool/scripts/setup_medic.py --reboot   # restart into it when done

For a fresh Raspberry Pi OS Lite (64-bit) card with this repository cloned to
~/reticulum-tool, run as the medic's own user (not with sudo; Raspberry Pi
OS's first user has the passwordless sudo it needs). It installs what Node
Medic 1 has, at Node Medic 1's versions, from assets/medic_manifest.json — the
same end state a cloned medic reaches, with the internet standing in for the
parent medic (workflows/medic_setup.py). One line per step; every finished
step is skipped on the next run, so after any failure the same command
carries on. Every command and its output goes to ~/medic_setup.log.

Exit status: 0 everything done, 1 something still missing or failed,
2 refused (not a Pi 5 / not 64-bit Lite trixie / not at ~/reticulum-tool).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from workflows.medic_setup import run_setup  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Set up this Raspberry Pi 5 as a Node Medic from GitHub alone.")
    parser.add_argument("--check", action="store_true",
                        help="only report what is still missing; change nothing")
    parser.add_argument("--reboot", action="store_true",
                        help="restart into Node Medic once every step is done")
    args = parser.parse_args(argv)
    return run_setup(check_only=args.check, reboot=args.reboot)


if __name__ == "__main__":
    sys.exit(main())
