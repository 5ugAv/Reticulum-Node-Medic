"""Enable the RNode firmware's built-in NeoPixel status LED for a board.

RNode_Firmware already ships the NeoPixel status-LED code; it is gated per board
by two directives inside that board's ``#elif BOARD_MODEL == BOARD_XXX`` block in
``Boards.h``::

    #define HAS_NP true
    const int pin_np = <GPIO>;

This patcher inserts those two lines at the end of the target board's pin list
(right after its ``const int pin_sclk = ...;``), reproducing the hand-proven
Heltec V4 recipe but anchored on the board's block rather than a brittle line
number. Idempotent and block-scoped, so it never touches another board's block.

CAUTION: the NeoPixel DATA pin must not collide with any other function on the
board (LoRa SPI, OLED I2C, GPS UART, buttons, ADC). GPIO47 is verified free on
the Heltec V4 ONLY — every other board needs its own pinout research first.

Usage:
    python3 apply_neopixel_patch.py path/to/Boards.h [--board BOARD_HELTEC32_V4] [--pin 47]
"""

from __future__ import annotations

import argparse
import sys

DEFAULT_BOARD = "BOARD_HELTEC32_V4"
DEFAULT_PIN = 47
HAS_NP_TRUE = "#define HAS_NP true"


def _block_bounds(lines, board):
    """``(start, end)`` line indices of the ``BOARD_MODEL == board`` preprocessor
    block. ``end`` is the next ``#elif``/``#else``/``#endif`` at the SAME nesting
    level (exclusive), so a nested ``#if HAS_NP == false ... #endif`` inside the
    block does not prematurely end it. ``None`` if the board is not present."""
    start = None
    for i, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("#") and "BOARD_MODEL ==" in ln and board in ln:
            start = i
            break
    if start is None:
        return None
    depth = 0
    for j in range(start + 1, len(lines)):
        s = lines[j].lstrip()
        if s.startswith("#if"):
            depth += 1
        elif s.startswith("#endif"):
            if depth == 0:
                return (start, j)
            depth -= 1
        elif depth == 0 and (s.startswith("#elif") or s.startswith("#else")):
            return (start, j)
    return (start, len(lines))


def _anchor(lines, start, end):
    """Index to insert after: the block's ``const int pin_sclk`` line, falling
    back to its last ``const int pin_`` line. ``None`` if the block has none."""
    sclk = last_pin = None
    for i in range(start, end):
        if "const int pin_sclk" in lines[i]:
            sclk = i
        if "const int pin_" in lines[i]:
            last_pin = i
    return sclk if sclk is not None else last_pin


def is_patched(contents, board=DEFAULT_BOARD, pin=DEFAULT_PIN):
    """True if *board*'s block already carries the NeoPixel directives."""
    lines = contents.splitlines()
    bounds = _block_bounds(lines, board)
    if not bounds:
        return False
    start, end = bounds
    block = "\n".join(lines[start:end])
    return HAS_NP_TRUE in block and f"pin_np = {pin}" in block


def apply_patch(contents, board=DEFAULT_BOARD, pin=DEFAULT_PIN):
    """Return *contents* with the NeoPixel directives added to *board*'s block
    (idempotent). Raises ValueError if the block or its pin anchor is missing."""
    if is_patched(contents, board, pin):
        return contents
    lines = contents.splitlines()
    bounds = _block_bounds(lines, board)
    if not bounds:
        raise ValueError(f"Board block {board} not found in Boards.h")
    start, end = bounds
    idx = _anchor(lines, start, end)
    if idx is None:
        raise ValueError(f"No pin anchor (const int pin_*) in {board} block")
    indent = lines[idx][:len(lines[idx]) - len(lines[idx].lstrip())]
    lines.insert(idx + 1, f"{indent}{HAS_NP_TRUE}")
    lines.insert(idx + 2, f"{indent}const int pin_np = {pin};")
    return "\n".join(lines) + ("\n" if contents.endswith("\n") else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Enable NeoPixel status LED in Boards.h")
    ap.add_argument("path", help="path to RNode_Firmware/Boards.h")
    ap.add_argument("--board", default=DEFAULT_BOARD,
                    help="BOARD_MODEL macro to patch (default Heltec V4)")
    ap.add_argument("--pin", type=int, default=DEFAULT_PIN,
                    help="NeoPixel data GPIO (default 47 — V4-verified)")
    args = ap.parse_args(argv)
    with open(args.path) as fh:
        contents = fh.read()
    if is_patched(contents, args.board, args.pin):
        print("Already patched.")
        return 0
    with open(args.path, "w") as fh:
        fh.write(apply_patch(contents, args.board, args.pin))
    print(f"Patched Boards.h: {args.board} NeoPixel on GPIO{args.pin} "
          f"(HAS_NP true, pin_np = {args.pin}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
