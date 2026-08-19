"""Which RTNode-2400 target the plugged-in board is, and whether to ask.

Kivy-free on purpose. These are the two decisions the birth screen makes about
an RTNode build before anything is written to hardware, and both were wrong on
the bench on 2026-08-18:

* the screen asked "V3 or V4?" under the words "they look identical to Node
  Medic", while printing ``/dev/ttyUSB0`` — a port only a V3 can produce; and
* every SUCCESSFUL build ended in "Not available for this board", because the
  gate compared a birth certificate's board name ("Heltec32 V3", the beacon
  vocabulary) against the literal string "Heltec LoRa32 v3", which it can never
  equal. The build wrote the record that then condemned the board.

Both are decisions about data, not about widgets, so they live here where the
suite can hold them to account without a window.
"""

from __future__ import annotations

from typing import List, Optional

from workflows.rtnode_build import target_for_board_key

#: The two boards the photo chooser offers. The T-Beam Supreme is a real
#: RTNODE_TARGETS entry but reaches the operator by its own button, so it must
#: not appear as a card here.
HELTEC_PAIR = ("heltec_v3", "heltec_v4")


def blocked_board(detected: Optional[dict]) -> Optional[str]:
    """The board's display name when it is positively identified AND has no
    RTNode-2400 build; ``None`` otherwise.

    FAILS OPEN. "Not identified" is not "cannot be built" — an ambiguous
    reading must reach the chooser and its confirmation gate, which are the
    real brick guard. Only a board we can name and know we have no build for is
    ever refused.
    """
    det = detected or {}
    key = det.get("board_key")
    if not key or target_for_board_key(key):
        return None
    from ui import board_images
    return board_images.label(key) or key


def identified_target(detected: Optional[dict]) -> Optional[str]:
    """The build target when detection has identified the board OUTRIGHT, else
    ``None`` — the only condition under which the chooser may be skipped.

    Requires ``board_key``, which ``ui.board_detect`` sets only when its
    shortlist came down to a single board. That is the strict test and it has
    to stay strict: a NATIVE-USB ESP32-S3 is a V4 *or* a Wireless Tracker *or*
    a T-Beam Supreme, and pre-picking the V4 there is how a Tracker walked into
    the V3/V4 cards on 2026-08-01. A bridge S3 has no such company — it is a
    V3 and nothing else — and an nRF52 that NAMES itself over USB (the T-Echo
    literally says "T-Echo") is likewise alone in its family.
    """
    key = (detected or {}).get("board_key")
    return target_for_board_key(key)


def target_options(detected: Optional[dict]) -> List[str]:
    """The board cards to show. One when the board is identified outright, both
    Heltecs when it is not — an ambiguous reading must never narrow the choice."""
    t = identified_target(detected)
    return [t] if t else list(HELTEC_PAIR)
