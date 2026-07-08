"""Heltec V4 NeoPixel RGB patch for RNode firmware.

When flashing a Heltec LoRa32 V4 as an RNode, ``Boards.h`` must enable the
on-board NeoPixel by defining ``HAS_NP true`` and ``pin_np = 47``. The V4's
NeoPixel definitions live near the end of the file, so detection requires the
``pin_np = 47`` line to appear past line index 440 (an early match belongs to a
different board block and does not count).

Usage:
    python3 apply_neopixel_patch.py path/to/Boards.h
"""

from __future__ import annotations

import sys

PIN_NP_LINE = "const int pin_np = 47;"
HAS_NP_TRUE = "#define HAS_NP true"
HAS_NP_FALSE = "#define HAS_NP false"
_MIN_LINE_INDEX = 440


def is_patched(contents: str) -> bool:
    """True if Boards.h already carries the V4 NeoPixel patch."""
    lines = contents.splitlines()
    has_np = any(HAS_NP_TRUE in line for line in lines)
    pin_np = any(
        "pin_np = 47" in line
        for idx, line in enumerate(lines)
        if idx > _MIN_LINE_INDEX
    )
    return has_np and pin_np


def apply_patch(contents: str) -> str:
    """Return *contents* with the NeoPixel patch applied (idempotent)."""
    lines = contents.splitlines()

    # Flip HAS_NP false -> true, or add it if absent.
    replaced = False
    for i, line in enumerate(lines):
        if HAS_NP_FALSE in line:
            lines[i] = line.replace(HAS_NP_FALSE, HAS_NP_TRUE)
            replaced = True
    if not replaced and not any(HAS_NP_TRUE in line for line in lines):
        lines.insert(0, HAS_NP_TRUE)

    # Ensure a pin_np = 47 definition exists past the V4 threshold.
    has_pin = any(
        "pin_np = 47" in line
        for idx, line in enumerate(lines)
        if idx > _MIN_LINE_INDEX
    )
    if not has_pin:
        # pad so the appended line lands past the threshold
        while len(lines) <= _MIN_LINE_INDEX + 1:
            lines.append("")
        lines.append(PIN_NP_LINE)

    return "\n".join(lines)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: apply_neopixel_patch.py path/to/Boards.h")
        return 2
    path = argv[0]
    with open(path) as fh:
        contents = fh.read()
    if is_patched(contents):
        print("Already patched.")
        return 0
    with open(path, "w") as fh:
        fh.write(apply_patch(contents))
    print("Patched Boards.h for Heltec V4 NeoPixel (HAS_NP true, pin_np = 47).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
