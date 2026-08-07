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
import re
import sys

DEFAULT_BOARD = "BOARD_HELTEC32_V4"
DEFAULT_PIN = 47
HAS_NP_TRUE = "#define HAS_NP true"

# --- WHICH PIN, ON WHICH BOARD -------------------------------------------
# The standing rule is that a NeoPixel data pin is VERIFIED free per board and
# never guessed, because a pin already owned by the LoRa modem or the bootloader
# looks fine and fails in the field. Until now this script enforced none of it:
# it would write any pin into any board's block, and its default is 47.
#
# On a Heltec V4, 47 is a free J2 header pin. On a RAK4631 the SAME NUMBER is
# the LoRa modem's DIO line — so running the V4 recipe against a RAK would break
# the radio in a way that reads as a firmware bug, on a board whose whole job is
# the radio. One flag away, with nothing to stop it.
#
# Numbers here are ARDUINO pin numbers as the firmware uses them. On the nRF52
# that is not the same as the P0.xx/P1.xx silicon name: WB_IO1 on the RAK is
# Arduino 17, which is P0.17.
VERIFIED_NP_PIN = {
    # Heltec V4: free J2 header pin. Proven on hardware — the case glows.
    "BOARD_HELTEC32_V4": 47,
    # RAK4631: WB_IO1 = Arduino 17 = P0.17, exposed on WisBlock SLOT A/B.
    # CROSS-CHECKED against the firmware's own pin list: the BOARD_RAK4631 block
    # (Boards.h 724-761 at d39339f) claims 9, 37, 38, 42, 43, 44, 45, 46, 47 and
    # the two base-board LEDs. 17 appears nowhere in it. Still NOT proven on
    # hardware — nothing has been soldered to WB_IO1 yet.
    "BOARD_RAK4631": 17,
}

#: Pins we KNOW are already owned on a given board, and what owns them. Writing
#: one of these is never a judgement call — it is a mistake with a known cost,
#: so it is refused outright rather than warned about.
CLAIMED_PINS = {
    "BOARD_RAK4631": {
        # CONFIRMED from the firmware source, not inferred: the BOARD_RAK4631
        # block declares `const int pin_dio = 47;`. On a Heltec V4 the same
        # number is the free header pin the whole recipe was built around.
        47: "the LoRa modem's DIO line",
        # The IO-slot pins labelled SPI are the on-board QSPI flash; WB_IO5/6
        # are the NFC pins; WB_IO2 controls the 3V3 rail that powers the slots.
        30: "the on-board QSPI flash", 31: "the on-board QSPI flash",
        9: "an NFC pin (WB_IO5)", 10: "an NFC pin (WB_IO6)",
        34: "the 3V3 rail control (WB_IO2) — pulling it kills the slots",
    },
}


class UnverifiedPin(ValueError):
    """The pin has not been verified free on this board."""


def check_pin(board: str, pin: int, force: bool = False) -> None:
    """Refuse a pin that is known-claimed, or unverified without an explicit
    override. Raises UnverifiedPin with a reason a human can act on."""
    owned = CLAIMED_PINS.get(board, {}).get(pin)
    if owned:
        raise UnverifiedPin(
            f"Pin {pin} on {board} is {owned}. Refusing — this is not a "
            f"preference, it is a known collision. Use "
            f"{VERIFIED_NP_PIN.get(board, 'a researched free pin')}.")
    known = VERIFIED_NP_PIN.get(board)
    if known is None and not force:
        raise UnverifiedPin(
            f"No verified NeoPixel pin recorded for {board}. Research its "
            f"pinout first, add it to VERIFIED_NP_PIN, or pass "
            f"--i-have-verified-this-pin to take responsibility.")
    if known is not None and pin != known and not force:
        raise UnverifiedPin(
            f"{board}'s verified free pin is {known}, not {pin}. Pass "
            f"--i-have-verified-this-pin if you have genuinely checked this one "
            f"against the board's own pinout.")


def _block_bounds(lines, board):
    """``(start, end)`` line indices of the board's PIN block. ``None`` if the
    board is not present.

    A board can be tested by ``#if BOARD_MODEL == ...`` in SEVERAL places. The
    RAK4631 appears twice in Boards.h: once at line 143 selecting its modem
    (``#define MODEM SX1262``, no pins at all) and once at line 724 declaring
    its pin list. Taking the first match found the modem block and then failed
    with "no pin anchor" — the Heltec V4 has only one such block, so this never
    showed up. Prefer the candidate that actually declares pins.

    ``end`` is the next ``#elif``/``#else``/``#endif`` at the SAME nesting level
    (exclusive), so a nested ``#if HAS_NP == false ... #endif`` inside the block
    does not prematurely end it."""
    starts = [i for i, ln in enumerate(lines)
              if ln.lstrip().startswith("#") and "BOARD_MODEL ==" in ln and board in ln]
    if not starts:
        return None
    bounds = [(s, _block_end(lines, s)) for s in starts]
    for s, e in bounds:
        if any("const int pin_" in lines[k] for k in range(s, e)):
            return (s, e)
    return bounds[0]


def _block_end(lines, start):
    """Index of the directive that closes the block opened at *start*."""
    depth = 0
    for j in range(start + 1, len(lines)):
        s = lines[j].lstrip()
        if s.startswith("#if"):
            depth += 1
        elif s.startswith("#endif"):
            if depth == 0:
                return j
            depth -= 1
        elif depth == 0 and (s.startswith("#elif") or s.startswith("#else")):
            return j
    return len(lines)


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


#: A ``#define HAS_NP <anything>`` line. Deliberately NOT matching
#: ``#if HAS_NP == false``, which the firmware also contains — see _block_bounds.
#: WHY THESE EXIST. The Heltec V4's block declares no HAS_NP at all, so the
#: original recipe — append ``#define HAS_NP true`` after the pin list — was
#: correct there and nobody looked further. The RAK4631's block DOES declare one
#: (``#define HAS_NP false``, Boards.h line 731 at firmware d39339f), ABOVE its
#: pin list. Appending a second definition below it is a macro redefinition: the
#: compiler warns, the last one happens to win, and the build looks fine until
#: something turns that warning into an error. Flip the existing line instead.
_HAS_NP_DEFINE = re.compile(r"^\s*#\s*define\s+HAS_NP\b")
#: A ``const int pin_np = N;`` declaration. Two of these in one block is a hard
#: C++ redefinition error, not a warning.
_PIN_NP_DECL = re.compile(r"^\s*const\s+int\s+pin_np\b")


def is_patched(contents, board=DEFAULT_BOARD, pin=DEFAULT_PIN):
    """True if *board*'s block carries EXACTLY the NeoPixel directives we want.

    Strict on purpose. A block holding two HAS_NP defines, or a stale
    ``pin_np`` from a different pin, is not "patched" — it is damaged, and
    saying so is what lets :func:`apply_patch` repair it instead of reporting
    "Already patched." over a block that will not compile."""
    lines = contents.splitlines()
    bounds = _block_bounds(lines, board)
    if not bounds:
        return False
    start, end = bounds
    block = lines[start:end]
    defines = [ln for ln in block if _HAS_NP_DEFINE.match(ln)]
    decls = [ln for ln in block if _PIN_NP_DECL.match(ln)]
    if len(defines) != 1 or len(decls) != 1:
        return False
    return ("true" in defines[0].split("HAS_NP", 1)[1]
            and f"pin_np = {pin}" in decls[0])


def apply_patch(contents, board=DEFAULT_BOARD, pin=DEFAULT_PIN):
    """Return *contents* with the NeoPixel directives set on *board*'s block.

    NORMALISES rather than appends: any existing HAS_NP defines and pin_np
    declarations in the block are reduced to exactly one of each. That makes it
    idempotent, and it repairs a block an older version of this script left
    doubly-defined. Raises ValueError if the block or its pin anchor is
    missing."""
    lines = contents.splitlines()
    bounds = _block_bounds(lines, board)
    if not bounds:
        raise ValueError(f"Board block {board} not found in Boards.h")
    start, end = bounds

    # Keep the FIRST HAS_NP where it stands (flipped true) so a board that
    # declares one among its other HAS_* flags keeps its house style; drop any
    # duplicate, and drop every pin_np so exactly one is re-added below.
    cleaned, seen_np = [], False
    for ln in lines[start:end]:
        if _HAS_NP_DEFINE.match(ln):
            if not seen_np:
                seen_np = True
                pad = ln[:len(ln) - len(ln.lstrip())]
                cleaned.append(f"{pad}{HAS_NP_TRUE}")
            continue
        if _PIN_NP_DECL.match(ln):
            continue
        cleaned.append(ln)

    idx = _anchor(cleaned, 0, len(cleaned))
    if idx is None:
        raise ValueError(f"No pin anchor (const int pin_*) in {board} block")
    indent = cleaned[idx][:len(cleaned[idx]) - len(cleaned[idx].lstrip())]
    if not seen_np:
        cleaned.insert(idx + 1, f"{indent}{HAS_NP_TRUE}")
    # pin_np goes after the anchor, but after the HAS_NP line when that sits
    # directly against it — otherwise the two would swap on a second run.
    if idx + 1 < len(cleaned) and _HAS_NP_DEFINE.match(cleaned[idx + 1]):
        idx += 1
    cleaned.insert(idx + 1, f"{indent}const int pin_np = {pin};")

    out = lines[:start] + cleaned + lines[end:]
    return "\n".join(out) + ("\n" if contents.endswith("\n") else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Enable NeoPixel status LED in Boards.h")
    ap.add_argument("path", help="path to RNode_Firmware/Boards.h")
    ap.add_argument("--board", default=DEFAULT_BOARD,
                    help="BOARD_MODEL macro to patch (default Heltec V4)")
    ap.add_argument("--pin", type=int, default=None,
                    help="NeoPixel data pin. Default: the verified pin for the "
                         "chosen board (Arduino numbering).")
    ap.add_argument("--i-have-verified-this-pin", action="store_true",
                    dest="force",
                    help="override the per-board pin check — only after "
                         "checking the board's own pinout")
    args = ap.parse_args(argv)
    # Default to the board's OWN verified pin rather than the V4's. Inheriting
    # 47 by default is what made a RAK one flag away from a broken radio.
    pin = args.pin if args.pin is not None else VERIFIED_NP_PIN.get(args.board)
    if pin is None:
        print(f"No verified NeoPixel pin for {args.board}; pass --pin with "
              f"--i-have-verified-this-pin.", file=sys.stderr)
        return 2
    try:
        check_pin(args.board, pin, force=args.force)
    except UnverifiedPin as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    args.pin = pin
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
