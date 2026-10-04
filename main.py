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

    # Native crashes (segfaults in Kivy/SDL/GL/serial C code) kill the app with
    # NO Python traceback — the 2026-07-30 silent BIRTH-screen death. Dump every
    # thread's Python stack to a crash file on any fatal signal so the next one
    # is diagnosable from the field.
    import faulthandler
    import os
    try:
        crash_log = open(os.path.expanduser("~/ui_crash.log"), "a")
        crash_log.write("\n--- session start pid %d ---\n" % os.getpid())
        crash_log.flush()
        faulthandler.enable(file=crash_log, all_threads=True)
    except OSError:
        faulthandler.enable()        # fall back to stderr

    # Import the UI lazily so headless environments never require Kivy.
    # THE PANEL'S ONE TOUCH PROVIDER (ui/touch_input.py, 2026-10-03): mtdev
    # on the Goodix, mouse provider off — decided here, before any window or
    # font import pulls kivy.core in, because Kivy reads [input] at window
    # creation. Logged so the road taken is in ui.log.
    from kivy.config import Config as _KivyConfig
    from kivy.logger import Logger as _KivyLogger
    from ui import touch_input
    _touch = touch_input.choose(_KivyConfig,
                                log=lambda m: _KivyLogger.info("Node Medic: " + m))
    # The verdict is kept where Self Diagnose can read it: a clone with a
    # different panel used to fall back to one finger in silence (ledger #97).
    try:
        with open("/dev/shm/nodemedic-touch", "w") as _fh:
            _fh.write(str(_touch or ""))
    except OSError:
        pass
    # Pick the global display font from the saved language BEFORE any screen is
    # built (DejaVu for Latin/Cyrillic, Noto Sans JP for Japanese).
    from ui.fonts import configure_fonts
    configure_fonts()
    from ui.app import ReticulumNodeMedicApp

    ReticulumNodeMedicApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
