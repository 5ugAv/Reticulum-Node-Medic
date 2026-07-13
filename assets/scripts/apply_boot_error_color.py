"""Change the RNode firmware boot-error LED from full white to dim red.

Stock ``led_indicate_boot_error()`` (Utilities.h) latches the NeoPixel to
``npset(0xFF, 0xFF, 0xFF)`` — white, all three channels — in an infinite loop.
Because a WS2812 LATCHES its colour, that white persists even after the board is
reset into the ROM bootloader for reflashing, so the constant current draw can
sag the USB rail and abort the flash (observed on a real V4: a stuck-white board
would not accept a flash until its LED was physically disconnected).

A single dim red channel signals the same "boot error" fault at a fraction of the
current, so a boot-errored board can still be reflashed. This patcher rewrites the
white ``npset`` inside ``led_indicate_boot_error()`` to ``npset(<red>, 0, 0)``.
Idempotent and scoped to that one function (the non-NeoPixel #else branch, which
blinks discrete LEDs, is left untouched).

Usage:
    python3 apply_boot_error_color.py path/to/Utilities.h [--red 0x40]
"""

from __future__ import annotations

import argparse
import re

FUNC = "led_indicate_boot_error"
WHITE_RE = re.compile(r"npset\(\s*0xFF\s*,\s*0xFF\s*,\s*0xFF\s*\)")
DEFAULT_RED = 0x40  # * NP_M (0.15) -> a dim, low-current red that stays visible


def _func_bounds(lines):
    """``(start, end)`` line indices of the ``led_indicate_boot_error`` function,
    found by brace-matching from its opening ``{``. ``None`` if absent."""
    start = next((i for i, l in enumerate(lines) if FUNC in l and "void" in l),
                 None)
    if start is None:
        return None
    depth = 0
    started = False
    for j in range(start, len(lines)):
        depth += lines[j].count("{") - lines[j].count("}")
        if "{" in lines[j]:
            started = True
        if started and depth == 0:
            return (start, j + 1)
    return (start, len(lines))


def is_patched(contents):
    """True if the boot-error function no longer sets full white."""
    lines = contents.splitlines()
    bounds = _func_bounds(lines)
    if not bounds:
        return False
    body = "\n".join(lines[bounds[0]:bounds[1]])
    return WHITE_RE.search(body) is None


def apply_patch(contents, red=DEFAULT_RED):
    """Return *contents* with the boot-error white replaced by dim red
    (idempotent). Raises ValueError if the function is missing."""
    lines = contents.splitlines()
    bounds = _func_bounds(lines)
    if not bounds:
        raise ValueError(f"{FUNC}() not found in Utilities.h")
    repl = f"npset(0x{red:02X}, 0x00, 0x00)"
    for i in range(bounds[0], bounds[1]):
        if WHITE_RE.search(lines[i]):
            lines[i] = WHITE_RE.sub(repl, lines[i])
    return "\n".join(lines) + ("\n" if contents.endswith("\n") else "")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Dim-red boot-error LED patch for RNode Utilities.h")
    ap.add_argument("path", help="path to RNode_Firmware/Utilities.h")
    ap.add_argument("--red", type=lambda x: int(x, 0), default=DEFAULT_RED,
                    help="red channel value before NP_M scaling (default 0x40)")
    args = ap.parse_args(argv)
    with open(args.path) as fh:
        contents = fh.read()
    if is_patched(contents):
        print("Already patched.")
        return 0
    with open(args.path, "w") as fh:
        fh.write(apply_patch(contents, args.red))
    print(f"Patched Utilities.h: boot-error LED -> dim red "
          f"(npset(0x{args.red:02X}, 0x00, 0x00)).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
