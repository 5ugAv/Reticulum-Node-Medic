"""Reticulum Node Medic — application entry point.

Launching the Kivy UI is deferred into ``main()`` so that importing this module
(for tests or headless use) never pulls in Kivy or opens a window.
"""

from __future__ import annotations

import sys
from typing import List, Optional


def build_headless_demo():
    """Return a (connection, profile) pair wired to the emulator.

    Useful for exercising the diagnostic/build/repair core without hardware
    or a display — the same code paths the UI drives.
    """
    from node_profile import NodeProfile
    from transport.connection import EmulatedConnection

    return EmulatedConnection(), NodeProfile()


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--version" in argv:
        print("Reticulum Node Medic 0.1.0")
        return 0

    # Import the UI lazily so headless environments never require Kivy.
    from ui.app import ReticulumNodeMedicApp

    ReticulumNodeMedicApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
