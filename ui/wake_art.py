"""Geometry for the waking-board scene, as plain numbers.

The operator's scene (2026-08-05): *"The Node Medic plugs a cable into the
board. Instead of electricity flowing, little Reticulum symbols flow through the
cable. The board gradually changes from grey to full colour. Finally the eyes
open."*

Pure data, no Kivy — so the same numbers can be rendered offline with PIL and
LOOKED at before anything reaches the medic. That habit exists because a scene
that reads fine in code has twice shipped wrong: a portrait medic, and a sprite
with 82px of invisible padding that threw every alignment out.

Coordinates are fractions of the animation box measured from its TOP-LEFT, the
way you'd describe a picture. The Kivy widget flips y once, at the edge.
"""

from __future__ import annotations

#: Medic on the left, board on the right, with a wide gap between them — that
#: gap IS the scene: it is where the cable runs and the symbols travel.
MEDIC_CENTRE = (0.150, 0.50)
MEDIC_HEIGHT = 0.86

BOARD_CENTRE = (0.760, 0.44)
BOARD_HEIGHT = 0.84

#: Where the cable leaves the medic, and where it lands on the board.
#: The board sprite is PORTRAIT with its USB-C socket at the BOTTOM CENTRE, so
#: the cable arrives from underneath. Aiming it at the board's left edge (the
#: first attempt) plugged into bare PCB.
MEDIC_PORT = (0.232, 0.60)
BOARD_PORT = (0.760, 0.88)

#: A cable hangs. The control point is pulled BELOW the straight line between
#: the ports (larger y = further down) so it sags under its own weight rather
#: than drawing as a taut wire, which reads as a circuit diagram.
CABLE_SAG = 0.150

#: Braid thickness as a fraction of box height.
CABLE_WIDTH = 0.055

#: How many Reticulum symbols are in flight, and how big. Few and large reads as
#: packets; many and small reads as noise on a wire.
SYMBOL_COUNT = 3
SYMBOL_SIZE = 0.185

#: Symbols only start once the cable is seated, and colour only starts once
#: symbols are arriving — cause before effect.
PLUG_DONE_AT = 0.16
COLOUR_STARTS_AT = 0.22
COLOUR_FULL_AT = 0.88

#: The eyes are the payoff, so they wait until the colour has essentially
#: arrived. Opening them early spends the ending too soon.
EYES_OPEN_AT = 0.90

#: The eyes sit ON THE BOARD'S SCREEN — the big dark rectangle is the face, so
#: it is the one place on a busy PCB where two eyes read as eyes rather than as
#: artefacts. Fractions of the board sprite from its top-left; the screen spans
#: roughly x 0.13-0.65, y 0.11-0.57, and these sit inside it.
EYE_LEFT = (0.29, 0.31)
EYE_RIGHT = (0.50, 0.31)
EYE_RADIUS = 0.058

#: Plug size, as a fraction of box height.
PLUG_HEIGHT = 0.20


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def ramp(f: float, start: float, end: float) -> float:
    """0 before *start*, 1 after *end*, linear between. The one shape every
    stage of this scene is built from."""
    if end <= start:
        return 1.0 if f >= end else 0.0
    return _clamp01((f - start) / (end - start))


def bezier(t: float, p0, p1, p2):
    """Quadratic Bezier — the cable's curve, and the path symbols ride along."""
    u = 1.0 - t
    return (u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1])


def cable_control():
    """The control point that gives the cable its sag."""
    mx, my = MEDIC_PORT
    bx, by = BOARD_PORT
    return ((mx + bx) / 2.0, (my + by) / 2.0 + CABLE_SAG)


def cable_points(steps: int = 28):
    """The cable as a polyline, for drawing."""
    c = cable_control()
    return [bezier(i / float(steps), MEDIC_PORT, c, BOARD_PORT)
            for i in range(steps + 1)]


def colour_fraction(f: float) -> float:
    """How much of the board's colour has arrived (0 grey → 1 full colour)."""
    return ramp(f, COLOUR_STARTS_AT, COLOUR_FULL_AT)


def plug_fraction(f: float) -> float:
    """How far the plug has travelled into the board's socket."""
    return ramp(f, 0.0, PLUG_DONE_AT)


def eye_openness(f: float) -> float:
    """0 shut, 1 wide. Deliberately quick — an eye opens, it does not fade."""
    return ramp(f, EYES_OPEN_AT, min(1.0, EYES_OPEN_AT + 0.07))


def symbol_positions(f: float, phase: float = 0.0):
    """Where each Reticulum symbol is right now, travelling medic → board.

    Returns ``(x, y, alpha)`` per symbol. They fade in as they leave the medic
    and out as they enter the board, so none of them pops into or out of
    existence in open air.
    """
    if f < PLUG_DONE_AT:
        return []
    c = cable_control()
    out = []
    for i in range(SYMBOL_COUNT):
        t = ((f - PLUG_DONE_AT) * 2.6 + phase + i / float(SYMBOL_COUNT)) % 1.0
        x, y = bezier(t, MEDIC_PORT, c, BOARD_PORT)
        # fade in over the first fifth, out over the last fifth
        a = min(1.0, t / 0.2, (1.0 - t) / 0.2)
        out.append((x, y, max(0.0, a)))
    return out
