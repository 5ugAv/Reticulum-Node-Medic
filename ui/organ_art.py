"""Which organ art the theatre implants, and where each one sits.

Pure data, no Kivy. Lives outside ui/widgets/surgery_anim.py for the reason
this codebase keeps rediscovering: the suite installs process-global Kivy stubs
covering only the submodules already in use, so a test that imports a widget to
reach one constant breaks collection for everything after it. Same split as
provisioning.pi_imager.hostnameify and ui.scroll_rule.

The sprites are the operator's own illustrations, cut from the Dr. Pi frames
they drew (2026-08-04). They are kept as SEPARATE files on purpose: the real
write has five stages, the drawn frames only ever showed two implants, and a
fixed frame sequence cannot narrate a variable-length operation. Separated,
each organ lands when its stage actually completes.
"""

from __future__ import annotations

import os

ORGAN_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "ui", "anim", "organs"))

#: organ key -> (sprite filename, fallback colour, short label).
#: The colour is only used when the file is missing — never a second source of
#: truth for what the organ looks like.
ORGANS = {
    "bootloader":  ("gear.png",    (0.62, 0.62, 0.64, 1), "boot"),
    "kernel":      ("brain.png",   (1.00, 0.42, 0.42, 1), "kernel"),
    "filesystem":  ("padlock.png", (0.85, 0.68, 0.25, 1), "files"),
    "reticulum":   ("mesh.png",    (0.45, 0.40, 0.65, 1), "mesh"),
    "identity":    ("shield.png",  (0.35, 0.55, 0.65, 1), "name"),
}

#: Drawn by the operator, no stage to land on yet. Kept so they are not lost.
SPARE_ORGANS = ("message.png", "wifi.png")

#: The dark panel on the card the organs sit in, as fractions of the card sprite
#: (x0, y0, x1, y1 from its TOP-LEFT). Without one, the first render put a gear
#: straight across the "64" on the SanDisk label (2026-08-04).
CARD_WINDOW = (0.06, 0.46, 0.62, 0.88)

#: Where each organ settles inside that window, left to right in the order the
#: real write completes them. A row: five organs scattered read as spilled
#: rather than implanted.
ORGAN_SEATS = {
    "bootloader": (0.12, 0.5),
    "kernel":     (0.30, 0.5),
    "filesystem": (0.50, 0.5),
    "reticulum":  (0.70, 0.5),
    "identity":   (0.88, 0.5),
}


def organ_file(key: str) -> str:
    """Absolute path to an organ sprite, or "" if the key is unknown."""
    art = ORGANS.get(key)
    return os.path.join(ORGAN_DIR, art[0]) if art else ""
