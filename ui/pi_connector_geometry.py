"""Where THIS Raspberry Pi's data socket is, on the picture we draw of it.

The "Connect the Pi to Node Medic" step exists to answer one question: which
hole does the cable go in. It answered it by ringing two sockets on a Pi Zero
2 W — always, whatever board the operator had chosen — because the ring
positions were fractions measured on the Zero sprite and nothing else had been
measured. Hand it another model and the board picture changes while the rings
stay put, pointing at bare PCB.

So it kept showing a Zero. On a bench holding a 3 A+, that is a picture of the
wrong board with its sockets in the wrong places, above words that correctly
describe the right one (operator, live 2026-08-09). The picture is what people
follow.

MEASURED, NOT LOOKED UP. Every entry here was read off the SPRITE THAT GETS
DRAWN, by rendering markers onto it and looking — the same method the SD slots
got (see ui.pi_sd_geometry). A datasheet describes the board; these fractions
have to describe the drawing, and the two are not the same thing.

A board with no entry is not guessed at. It gets its own true picture with no
rings on it, and the per-model wording in ui.pi_connectors carries the answer —
honest about what is known, which is more use than a confident lie. Add models
here as they pass the bench, one measurement at a time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class Sockets:
    """Socket positions on one board's sprite, as fractions with y DOWN."""
    key: str
    #: The sprite these were measured on. Re-measure if the art is replaced —
    #: the numbers describe the picture, not the product.
    art: str
    #: Centre of the socket the cable goes in.
    data: Tuple[float, float]
    #: Centre of the power-in socket, when it is a different one. None when the
    #: board takes power and data through the same port (Pi 4B / 5 USB-C).
    power: Optional[Tuple[float, float]]
    #: Which way a plug travels to enter the data socket: "bottom" = it rises
    #: into the board's lower edge; "right" = it slides in from the right edge.
    approach: str


#: Boards measured on the bench. Nothing else belongs here.
MEASURED: Dict[str, Sockets] = {
    # Zero 2 W: two identical micro-USB shells on the bottom edge. The INNER
    # one (nearer the mini-HDMI) is the data port; the outer is PWR IN and
    # cannot carry data. Measured on pi_zero_2w_cut.png, 2026-08-09 — these are
    # the fractions the animation had been using all along, confirmed rather
    # than assumed.
    "pi_zero_2w": Sockets("pi_zero_2w", "pi_zero_2w_cut.png",
                          data=(0.626, 0.93), power=(0.819, 0.93),
                          approach="bottom"),
    # 3 A+: the data path is the full-size USB-A on the RIGHT edge — a
    # different socket on a different edge, entered from a different direction.
    # Its micro-USB, bottom-left, is power only. Measured on pi_3a_plus.png,
    # 2026-08-09.
    "pi_3a_plus": Sockets("pi_3a_plus", "pi_3a_plus.png",
                          data=(0.85, 0.41), power=(0.215, 0.925),
                          approach="right"),
}


def sockets_for(pi_key: str) -> Optional[Sockets]:
    """The measured socket geometry for *pi_key*, or None if we have not
    measured that board. None means "draw it honestly without markers", never
    "use the Zero's numbers"."""
    return MEASURED.get((pi_key or "").strip())


def is_measured(pi_key: str) -> bool:
    return sockets_for(pi_key) is not None
