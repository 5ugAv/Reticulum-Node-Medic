"""Where each Raspberry Pi's microSD slot is, and how a card gets into it.

The standing rule this exists to serve (operator, restated as absolute
2026-08-06): *"All images that the user sees on Node Medic should correspond to
the hardware they've got in their hand."* It was triggered by the guide showing
a Pi Zero photo while a 3A+ was selected. A card sliding into the wrong EDGE of
a board is the same fault one layer down — the operator is checking the screen
against the thing in their hand, and a wrong edge tells them the medic has
misidentified their board.

So no animation may hardcode one Pi's layout. Everything the drawing needs is
data here, per model:

    which EDGE of the board (as photographed) the slot opens on
    how far ALONG that edge it sits, and how wide its mouth is
    which FACE of the board the holder is mounted on — top or underside
    whether the card goes in label-toward-you
    friction-fit or push-push (a push-push slot CLICKS and springs back)

Pure data + pure math: no Kivy, no image loading, so the kinematics are unit
testable and the same functions drive both the on-device animation and the
offline PIL preview renderer (``scripts/preview_sd_handover.py``). Rendering
what you previewed is the whole point — scenes that read fine in code have
shipped wrong twice before, so the geometry lives in one place and both
renderers ask it.

COORDINATES. Rects are ``(x, y, w, h)`` with y measured UP from the bottom
(Kivy's frame), because that is what the widget draws in; the previewer flips
once at the end. The one exception is ``along``/``board_box``, which are
fractions measured y-DOWN from a sprite's top-left — that is how every other
sprite fraction in this codebase is measured (see ``board_images.oled``,
``birth_anims._PLUG_TIP``) and breaking with it would guarantee a sign error.

PROVISIONAL VALUES. Only ``pi_zero_2w`` is measured — off its own sprite, and
tuned on the live screen. Every other model's slot position below is a
placeholder marked ``provisional=True``: the shape of the mechanism is right,
the numbers are not yet trustworthy. ``docs/pi_sd_slot_geometry.md`` is the
source of truth once it exists; this module reads it at import and overrides
whatever it defines (see ``parse_doc``). Until then, ``provisional_keys()``
names everything still guessed, so a caller — or a test, or a person — can ask.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple

#: The measured-geometry document another pass produces. Absent is normal.
DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "pi_sd_slot_geometry.md")

EDGES = ("left", "right", "top", "bottom")
FACES = ("top", "underside")
RETENTIONS = ("friction", "push_push")


@dataclass(frozen=True)
class SlotGeometry:
    """One Pi model's microSD slot, expressed against the sprite we draw.

    ``along``/``mouth``/``inset`` are fractions of the BOARD, not of the sprite
    canvas — the board photos carry ~9% transparent padding down their left
    side, so canvas fractions would put the card's entry point off the board's
    edge entirely. ``board_box`` says where the board actually is inside the
    sprite, and the conversion happens in :func:`board_rect`.

    ``board_box`` MAY fall outside 0→1. Three of the four photos are cropped
    tight enough that the board bleeds off the frame, so the honest answer for
    "where is the board" is partly outside the picture. Only the left PCB edge
    is fully in shot on all of them — which is, conveniently, the only edge any
    of these boards puts its card slot on.
    """

    #: Which edge of the board, AS PHOTOGRAPHED, the slot mouth opens on.
    edge: str
    #: Centre of the mouth along that edge, 0→1. For left/right edges measured
    #: from the board's TOP downward; for top/bottom edges from its LEFT. This
    #: is the geometry doc's ``v``, which is measured from the GPIO edge — the
    #: same thing, since every board here is drawn GPIO-up.
    along: float
    #: The mouth's span along the edge, as a fraction of that edge's length.
    #: The holder is 11.5 mm wide (an 11 mm card plus its walls), so this is
    #: 11.5 over the edge in mm.
    mouth: float
    #: How far in from the edge the mouth LINE sits, as a fraction of the
    #: board's other dimension. The card vanishes at this line, not at the
    #: board outline: on the 56 mm boards the holder is set back about 3 mm, so
    #: the card crosses a little bare board before it disappears.
    inset: float
    #: Which face the holder is mounted on. "underside" changes the drawing,
    #: not just a label: the card must pass BEHIND the board and be hidden by
    #: it, because that is what the operator will see when they do it.
    face: str
    #: "friction" (pull it straight out) or "push_push" (it clicks and springs).
    #: NOTHING THIS TOOL SUPPORTS IS push_push — the spring-eject socket was
    #: dropped at the Pi 3 Model B in 2016. The field stays because it is a real
    #: property of a slot and the doc records it; the animation must never play
    #: a click, because none of these boards gives one.
    retention: str
    #: Does the printed label face the viewer as the card goes in, from the side
    #: the photo shows? Contacts always face the PCB, so this follows ``face``:
    #: true for the Zero's top-mounted holder, false for every underside one.
    card_face_up: bool
    #: The sprite these fractions were measured against, for the guard test.
    sprite: str
    #: (x0, y0, x1, y1) of the board within that sprite, fractions, y-DOWN.
    board_box: Tuple[float, float, float, float]
    #: (long, short) board size in mm, from the official mechanical drawings.
    #: Lets a test check the board box against the real aspect ratio, which is
    #: how a re-crop gets caught.
    board_mm: Tuple[float, float]
    #: True while the numbers are a placeholder rather than a measurement.
    provisional: bool = False
    note: str = ""


def _g(**kw) -> SlotGeometry:
    return SlotGeometry(**kw)


#: Keyed by the Pi art key used everywhere else (``board_images.image_for_pi``,
#: ``pi_usbboot.art_key``).
#:
#: ALL FIVE come from docs/pi_sd_slot_geometry.md, which measured each board
#: individually against its own underside photograph and mounting holes. The
#: headline of that work: four of the five are the SAME animation — the slot is
#: centred on the left short edge, on the underside — and the Zero 2 W is the
#: only outlier, higher up its edge and on the TOP face. That top-versus-
#: underside split is not a detail; it flips which way up the card goes.
PI_SLOTS: Dict[str, SlotGeometry] = {
    # The CUT sprite, not the studio shot: the uncut one still has its white
    # background, and on this dark UI the board would arrive in a white box.
    # Both crops hold the same 1475 x 671 px of board, so board-relative
    # fractions carry over between them unchanged.
    #
    # along/inset here are the numbers the operator tuned on the live 5" panel
    # (2026-08-02) rather than the doc's 0.41 / flush-to-the-edge. They agree to
    # within a millimetre of real board, and these are the ones that were
    # actually looked at on the screen they are drawn on.
    "pi_zero_2w": _g(
        edge="left", along=0.4313, mouth=0.4136, inset=0.0234,
        face="top", retention="friction", card_face_up=True,
        sprite="assets/ui/anim/pi_zero_2w_cut.png",
        board_box=(0.0027, 0.0056, 0.9808, 0.9507), board_mm=(65.0, 30.0),
        note="Holder on the TOP face at the left short edge — the only one of "
             "the five where the operator can see it, and the only one where "
             "the card goes in label-up."),
    "pi_3a_plus": _g(
        edge="left", along=0.50, mouth=0.2054, inset=0.046,
        face="underside", retention="friction", card_face_up=False,
        sprite="assets/boards/pi_3a_plus.png",
        board_box=(0.088, -0.014, 0.8532, 0.975), board_mm=(65.0, 56.0),
        note="v=0.4995 off an underside photo carrying FCC ID 2ABCB-RPI3AP."),
    "pi_3b_plus": _g(
        edge="left", along=0.48, mouth=0.2054, inset=0.0353,
        face="underside", retention="friction", card_face_up=False,
        sprite="assets/boards/pi_3b_plus.png",
        board_box=(0.086, -0.008, 0.9916, 0.887), board_mm=(85.0, 56.0),
        note="v=0.476 (+/-0.02, the source photo is hand-held). NO ARTWORK "
             "ships for this model, so the animation draws its generic board "
             "rather than another Pi; the box here is the 4B's, since the two "
             "boards are the same 85x56 outline, and must be re-measured if a "
             "photo ever lands."),
    "pi_4b": _g(
        edge="left", along=0.49, mouth=0.2054, inset=0.0353,
        face="underside", retention="friction", card_face_up=False,
        sprite="assets/boards/pi_4b.png",
        board_box=(0.086, -0.008, 0.9916, 0.887), board_mm=(85.0, 56.0),
        note="v=0.491 off an underside photo with a card seated in the slot. "
             "NOT down by the USB-C corner, which several sources claim."),
    "pi_5": _g(
        edge="left", along=0.49, mouth=0.2054, inset=0.0353,
        face="underside", retention="friction", card_face_up=False,
        sprite="assets/boards/pi_5.png",
        board_box=(0.090, 0.001, 1.0316, 0.932), board_mm=(85.0, 56.0),
        note="v=0.494. The Pi 5 did NOT move its slot to another edge, whatever "
             "the secondary sources say."),
}


#: Same physical board, so same slot (the 3 A / 5 A split is about the SUPPLY).
ALIASES = {"pi_5_full": "pi_5"}


# --------------------------------------------------------------------------- #
# lookup
# --------------------------------------------------------------------------- #

def geometry_for(pi_key: str) -> Optional[SlotGeometry]:
    """This model's slot, or None when we don't know the model.

    None is a real answer and callers must honour it: an unknown Pi gets the
    generic board drawing, never another model's edge. Showing the card going
    into a plausible-looking wrong place is worse than showing no board at all,
    for the same reason a photo of the wrong Pi is.
    """
    key = (pi_key or "").strip()
    key = ALIASES.get(key, key)
    geo = _WITH_DOC.get(key)
    return geo


def sprite_path(geo: SlotGeometry) -> Optional[str]:
    """Absolute path to the artwork this geometry was measured against, if the
    file is there.

    The sprite and the numbers are one unit: ``board_box`` says where the board
    sits inside THAT crop, so pairing the fractions with a different photo of
    the same model puts the slot in the wrong place. So the animation asks for
    this file and no other — a new photo has to be measured before it is used,
    rather than silently inheriting somebody else's box.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, geo.sprite)
    return path if os.path.exists(path) else None


def provisional_keys() -> Tuple[str, ...]:
    """Models whose numbers are still placeholders. Ships as a warning route:
    a screen (or a test, or a person) can ask before trusting the picture."""
    return tuple(sorted(k for k, g in _WITH_DOC.items() if g.provisional))


# --------------------------------------------------------------------------- #
# pure geometry
# --------------------------------------------------------------------------- #

def board_rect(geo: SlotGeometry, sprite_rect) -> Tuple[float, float, float, float]:
    """The BOARD's rect inside a drawn sprite rect (both y-UP ``x, y, w, h``).

    The board photos are padded with transparency — 145 px of 1536 down the
    left of every assets/boards shot — so "the left edge of the sprite" and
    "the left edge of the board" are 9% of the width apart. Aligning a card to
    the sprite instead of the board is precisely the invisible-padding trap
    that threw an earlier animation out (2026-08-02).
    """
    sx, sy, sw, sh = sprite_rect
    x0, y0, x1, y1 = geo.board_box
    return (sx + x0 * sw,
            sy + (1.0 - y1) * sh,            # y-DOWN fraction -> y-UP origin
            (x1 - x0) * sw,
            (y1 - y0) * sh)


def entry_vector(geo: SlotGeometry) -> Tuple[float, float]:
    """Unit vector (y-UP) the card TRAVELS along as it goes in."""
    return {"left": (1.0, 0.0), "right": (-1.0, 0.0),
            "top": (0.0, -1.0), "bottom": (0.0, 1.0)}[geo.edge]


def entry_angle(geo: SlotGeometry) -> float:
    """Degrees CCW to rotate the card sprite by.

    The card artwork's leading (contact) edge points +x — the chamfered corner
    is on the right of ``sd_card_endurance.png`` — so the sprite rotation IS
    the travel direction's angle, and a slot on a different edge simply comes
    out as a different angle rather than as different drawing code.
    """
    dx, dy = entry_vector(geo)
    return math.degrees(math.atan2(dy, dx)) % 360.0


def slot_mouth(geo: SlotGeometry, brect) -> Tuple[float, float, float]:
    """``(cx, cy, span)`` — the mouth's centre in the board rect, and its width
    across the travel direction."""
    bx, by, bw, bh = brect
    if geo.edge in ("left", "right"):
        cy = by + bh * (1.0 - geo.along)     # along runs DOWN from the top
        cx = bx + geo.inset * bw if geo.edge == "left" else bx + bw - geo.inset * bw
        return (cx, cy, geo.mouth * bh)
    cx = bx + geo.along * bw                 # along runs RIGHT from the left
    cy = by + geo.inset * bh if geo.edge == "bottom" else by + bh - geo.inset * bh
    return (cx, cy, geo.mouth * bw)


def card_size(geo: SlotGeometry, brect, sprite_aspect: float = 1.324
              ) -> Tuple[float, float]:
    """``(length, width)`` of the card on screen, derived from the slot mouth.

    The card is sized by the hole it goes into rather than by the stage, so it
    is automatically right for whichever board is being drawn — an 11 mm card
    in an 11 mm mouth, at whatever scale that board happens to be shown. 0.92
    because the metal cage is a hair wider than the card (the number the Pi
    Zero step already used).
    """
    _cx, _cy, span = slot_mouth(geo, brect)
    width = span * 0.92
    return (width * sprite_aspect, width)


def half_plane(point, normal, bounds) -> list:
    """Four corner points of the part of *bounds* on the *normal* side of the
    line through *point*. Fed to a stencil Quad on device and to a polygon mask
    in the previewer, so both clip identically.

    Only axis-aligned and 45°-ish normals occur here (board edges, and the card
    reader's angled slot), so a rect-clip is enough: extend the line across the
    bounds and take the half that keeps the card's VISIBLE part.
    """
    bx, by, bw, bh = bounds
    px, py = point
    nx, ny = normal
    if abs(nx) >= abs(ny):                   # vertical-ish line
        if nx >= 0:                          # keep everything right of it
            return [px, by, bx + bw, by, bx + bw, by + bh, px, by + bh]
        return [bx, by, px, by, px, by + bh, bx, by + bh]
    # horizontal-ish line: extend it across the bounds using its slope
    slope = -nx / ny if ny else 0.0
    yl = py + slope * (bx - px)
    yr = py + slope * (bx + bw - px)
    if ny >= 0:                              # keep everything above it
        return [bx, yl, bx + bw, yr, bx + bw, by + bh, bx, by + bh]
    return [bx, by, bx + bw, by, bx + bw, yr, bx, yl]


# --------------------------------------------------------------------------- #
# the handover's kinematics
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Frame:
    """Where the card is at one instant, which way up, and how to clip it."""
    cx: float
    cy: float
    angle: float                 # degrees CCW; 0 = leading edge pointing +x
    behind_board: bool           # underside slot -> the board hides the card
    clip_point: Optional[Tuple[float, float]] = None
    clip_normal: Optional[Tuple[float, float]] = None
    seated: float = 0.0          # 0 clear of the slot, 1 fully home
    #: Foreshortening across the card's short axis, 1 flat-on and 0 edge-on.
    #: The card TURNING OVER mid-air is drawn by squeezing this to nothing and
    #: back out again, which is what a flip looks like from straight ahead.
    face_scale: float = 1.0
    #: True once it has turned past edge-on and the CONTACT side is toward the
    #: viewer. The artwork is the label face, so this is the renderer's cue to
    #: draw the reverse instead of the sprite.
    showing_back: bool = False


#: Phase boundaries. Out of the reader, across, in — with a beat of dwell at
#: the end so the loop doesn't snap straight back to the reader.
_OUT_END = 0.26
_TRAVEL_END = 0.66
_IN_END = 0.90

#: How far the card is drawn back out of the reader, in card lengths.
_OUT_PULL = 0.60

#: How far a seated card stands proud of the PCB EDGE, in millimetres. With a
#: friction socket that overhang is the only thing there is to grip, so it is
#: not a rounding error — it is the part of the card the operator will pull on.
#: A fixed fraction of the card's length was tried first and it buried the card
#: completely on the underside boards: their mouth is set back about 3 mm, so
#: everything that stuck out past the MOUTH was still hidden behind the board
#: (offline render, pi_4b, nothing visible at all). Measuring from the edge, in
#: the board's own millimetres, is what makes it come out right on every model.
PROUD_MM = 3.0

#: When the card turns over, as a fraction of the whole loop. It happens in
#: mid-air during the crossing, which is where a person does it.
_FLIP_FROM, _FLIP_TO = 0.40, 0.56


def flip_state(t: float, geo, span=(_FLIP_FROM, _FLIP_TO)):
    """``(face_scale, showing_back)`` for a card that has to be turned over.

    THE ANSWER TO THE LABEL PROBLEM, and it is not a rendering trick — it is
    the instruction. Contacts always face the PCB, so on the four underside
    boards the card goes in label DOWN: a top-view Pi 4 with a label-up card
    sliding into it is a picture of the operator doing it wrong, which is the
    one thing this whole feature exists to prevent.

    Three ways out were on the table: mirror the sprite, draw those boards from
    below, or find a framing that dodges the question. Mirroring shows
    back-to-front lettering, which is a rendering artefact rather than the
    other side of a card. Drawing the boards from below means underside
    photographs we do not have and must not fake. So the card turns over on its
    way across — the label face leaves the reader, the card flips in mid-air,
    and the CONTACT face goes into the board.

    That costs one drawn card-back (see :func:`card_back_shapes`) and it earns
    something the other options do not: it teaches the operator the step they
    are most likely to get wrong, without a word of text.

    A Zero 2 W never flips. Its holder is on the top face, so the card goes in
    label up exactly as it came out of the reader — and the difference between
    the two kinds of board is then visible in the animation itself.
    """
    if geo.card_face_up:
        return (1.0, False)
    lo, hi = span
    if t <= lo:
        return (1.0, False)
    if t >= hi:
        return (1.0, True)
    u = (t - lo) / (hi - lo)
    return (abs(math.cos(math.pi * u)), u > 0.5)


#: The reverse of a microSD, in the card's own local frame: an off-white body
#: and eight gold contact pads along the leading edge. Kept here, as data, so
#: the widget and the offline previewer draw the same card back rather than two
#: different ideas of one.
CARD_BACK_RGB = (0.90, 0.90, 0.88)
CARD_PAD_RGB = (0.83, 0.69, 0.28)


def card_back_shapes(card_len: float, card_w: float):
    """``(body, [pads])`` as ``(x, y, w, h)`` rects centred on (0, 0), with +x
    the leading (contact) edge — rotate them the same way as the sprite.

    Eight pads is not decoration: it is what the side of the card the operator
    must have facing the board actually looks like, and it is the only thing
    distinguishing this from a blank rectangle.
    """
    body = (-card_len / 2.0, -card_w / 2.0, card_len, card_w)
    pad_l = card_len * 0.20
    x0 = card_len * 0.5 - card_len * 0.03 - pad_l
    inner = card_w * 0.86
    slot = inner / 8.0
    pads = []
    for i in range(8):
        y = -inner / 2.0 + i * slot + slot * 0.15
        pads.append((x0, y, pad_l, slot * 0.70))
    return body, pads


def _ease(v: float) -> float:
    v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
    return v * v * (3.0 - 2.0 * v)           # smoothstep


def _lerp(a, b, t):
    return a + (b - a) * t


def _shortest(a: float, b: float) -> float:
    """b expressed as a±<=180 so the card takes the short way round."""
    d = (b - a + 180.0) % 360.0 - 180.0
    return a + d


def _across(geo, brect):
    """The board's size along the card's direction of travel, in pixels."""
    _bx, _by, bw, bh = brect
    return bw if geo.edge in ("left", "right") else bh


def seated_centre(geo, brect, card_len) -> Tuple[float, float]:
    """Where the card's centre ends up once it is home — measured from the PCB
    EDGE, so that PROUD_MM of it is still outside the board."""
    cx, cy, _span = slot_mouth(geo, brect)
    dx, dy = entry_vector(geo)
    across = _across(geo, brect)
    edge_x = cx - dx * geo.inset * across          # back out to the PCB edge
    edge_y = cy - dy * geo.inset * across
    proud = PROUD_MM / geo.board_mm[0] * across
    d = max(card_len * 0.1, card_len / 2.0 - proud)
    return (edge_x + dx * d, edge_y + dy * d)


def approach_centre(geo, brect, card_len, gap=0.55) -> Tuple[float, float]:
    """Lined up with the slot, clear of the board, ready to go in."""
    cx, cy, _span = slot_mouth(geo, brect)
    dx, dy = entry_vector(geo)
    d = card_len * (0.5 + gap)
    return (cx - dx * d, cy - dy * d)


def insert_frame(t: float, geo, brect, card_len, gap=0.55) -> Frame:
    """The simple case: the card is already in hand, and goes in.

    Used by the 'put the card in the Pi' step, which has no reader in it. The
    turn-over happens at the START here, while the card is still clear of the
    board — same instruction as the handover's mid-air flip, just earlier,
    because there is no journey to fit it into.
    """
    a = entry_angle(geo)
    sx, sy = approach_centre(geo, brect, card_len, gap)
    ex, ey = seated_centre(geo, brect, card_len)
    p = _ease(t)
    mx, my, _span = slot_mouth(geo, brect)
    nx, ny = entry_vector(geo)
    scale, back = flip_state(t, geo, span=(0.05, 0.28))
    return Frame(cx=_lerp(sx, ex, p), cy=_lerp(sy, ey, p), angle=a,
                 behind_board=(geo.face == "underside"),
                 clip_point=(mx, my), clip_normal=(-nx, -ny), seated=p,
                 face_scale=scale, showing_back=back)


def handover_frame(t: float, geo, brect, card_len,
                   reader_seat, reader_axis_deg: float) -> Frame:
    """The full handover: out of Node Medic's reader, across, into the Pi.

    *reader_seat* is the card's centre while it is still in the reader, and
    *reader_axis_deg* the direction it slides OUT along — both supplied by the
    caller because the medic's card-reader form factor is not settled yet (a
    built-in reader is on the cards; see the operator's permanent-SD-reader
    plan). Keeping them as arguments means that change is a layout edit, not a
    rewrite of the motion.

    The card keeps ONE orientation story throughout: it leaves the reader
    trailing-edge-first (which is what pulling a card out looks like), turns
    while it travels, and arrives leading-edge-first at the slot. So the
    rotation is between two real orientations rather than decoration.
    """
    a_read = reader_axis_deg
    a_slot = _shortest(a_read, entry_angle(geo))
    rx, ry = reader_seat
    ux, uy = math.cos(math.radians(a_read)), math.sin(math.radians(a_read))
    # out of the reader: straight back along the slot axis, far enough to be
    # clear of the mouth and no further — the reader's slot points down-and-
    # left on this artwork, so every extra millimetre of pull-out is spent
    # heading for the bottom of a stage only 150 dp tall.
    ox, oy = rx - ux * card_len * _OUT_PULL, ry - uy * card_len * _OUT_PULL
    ax, ay = approach_centre(geo, brect, card_len)
    ex, ey = seated_centre(geo, brect, card_len)
    mx, my, _span = slot_mouth(geo, brect)
    nx, ny = entry_vector(geo)
    behind = geo.face == "underside"
    scale, back = flip_state(t, geo)

    if t < _OUT_END:                                   # 1. sliding out
        p = _ease(t / _OUT_END)
        # clipped at the READER's mouth so it emerges rather than appears
        return Frame(cx=_lerp(rx, ox, p), cy=_lerp(ry, oy, p), angle=a_read,
                     behind_board=False,
                     clip_point=reader_seat, clip_normal=(-ux, -uy),
                     face_scale=scale, showing_back=back)
    if t < _TRAVEL_END:                                # 2. crossing over,
        p = _ease((t - _OUT_END) / (_TRAVEL_END - _OUT_END))   # turning over
        # a quadratic arc, lifted so the card doesn't scrape along the stage
        # floor between the two objects
        lift = abs(ax - ox) * 0.18
        mxx = (ox + ax) / 2.0
        myy = max(oy, ay) + lift
        u = 1.0 - p
        return Frame(cx=u * u * ox + 2 * u * p * mxx + p * p * ax,
                     cy=u * u * oy + 2 * u * p * myy + p * p * ay,
                     angle=_lerp(a_read, a_slot, p), behind_board=behind,
                     face_scale=scale, showing_back=back)
    if t < _IN_END:                                    # 3. going in
        p = _ease((t - _TRAVEL_END) / (_IN_END - _TRAVEL_END))
        return Frame(cx=_lerp(ax, ex, p), cy=_lerp(ay, ey, p), angle=a_slot,
                     behind_board=behind,
                     clip_point=(mx, my), clip_normal=(-nx, -ny), seated=p,
                     face_scale=scale, showing_back=back)
    # 4. home, and it STAYS there. No click, no spring-back, no settle: the
    # push-push socket was dropped at the Pi 3 Model B in 2016 and not one board
    # this tool supports has one. An animation that springs would be claiming
    # something the hardware does not do, and the operator would push a seated
    # card expecting an eject.
    return Frame(cx=ex, cy=ey, angle=a_slot,
                 behind_board=behind, clip_point=(mx, my),
                 clip_normal=(-nx, -ny), seated=1.0,
                 face_scale=scale, showing_back=back)


# --------------------------------------------------------------------------- #
# staging the handover scene
# --------------------------------------------------------------------------- #

#: Sprite heights as fractions of the stage. The stage is WIDE AND SHORT
#: (roughly 760x150 dp on the 5" panel once the title, body and buttons have
#: taken their share), which is the constraint every one of these numbers is
#: fighting: anything tall gets shrunk to a sliver, so the scene is laid out
#: left-to-right and nothing is stacked.
MEDIC_H = 0.94
#: The Pi Zero is a 2.2:1 board, so a tall setting drives everything else off
#: the stage: its slot is 41% of a short edge, the card is sized by that slot,
#: the reader is sized by the card, and the card's pull-out then starts BELOW
#: the stage floor. 0.72 is where an 11 mm card on a 56 mm board edge is still
#: legible and the Zero's pull-out still fits. Measured on the offline render,
#: not guessed.
PI_H = 0.72
#: Smaller when the slot faces up or down, because then the card's run-up has
#: to fit ABOVE or BELOW the board inside the same short stage. Kivy does not
#: clip a widget's canvas, so a card that starts off the bottom of the stage
#: does not vanish — it is drawn over the body text underneath (the trap the
#: connect-Pi step's control points are pinned for).
PI_H_EDGEWISE = 0.60
#: Never let the board crowd out the journey — the card crossing the gap IS
#: the instruction.
PI_MAX_W = 0.40
#: A microSD is 11 mm against a 56 mm board edge, so a physically-sized card
#: comes out small here — about a fifth of the board's edge. Sizing it by its
#: own slot is what keeps the picture honest; this floor only catches the case
#: where that would leave a card too small to recognise. On the five boards we
#: draw it does not bite, which is the intention. Set by looking at the render.
CARD_MIN_W = 0.13
#: Where the card sits inside sd_reader_body.png (fractions, y-DOWN) and the
#: direction it slides OUT along. Derived from InsertSdAnim's own measured card
#: path into that same sprite — its card travels (+205, -368) source px going
#: in, i.e. 61° up-and-right, matching the angle the reader is drawn at.
#:
#: This is the part of the scene most likely to change: Node Medic is likely to
#: gain a BUILT-IN card reader, and the guided insert animation was explicitly
#: left unfinished until that form factor is decided. Keeping the medic side in
#: two constants means that change is a layout edit.
READER_SEAT = (0.30, 0.62)
READER_AXIS_DEG = 61.0


@dataclass(frozen=True)
class Layout:
    """Every rect the handover scene needs, in y-UP stage coordinates."""
    medic: Optional[Tuple[float, float, float, float]]
    reader: Tuple[float, float, float, float]
    pi: Tuple[float, float, float, float]
    board: Tuple[float, float, float, float]
    card_len: float
    card_w: float
    reader_seat: Tuple[float, float]


def handover_layout(stage, geo: SlotGeometry, pi_aspect: float,
                    card_aspect: float, reader_aspect: float,
                    reader_slot_frac: float,
                    medic_aspect: Optional[float] = None) -> Layout:
    """Place the medic, its reader and the Pi across *stage* ``(x, y, w, h)``.

    Pure, and shared by the widget and the offline previewer — so the frames
    that were LOOKED at are the frames that ship. That is not ceremony: a
    portrait-oriented medic and a sprite with 82 px of invisible padding both
    reached the device because the only place the scene existed was in code.
    """
    x, y, w, h = stage
    edgewise = geo.edge in ("top", "bottom")
    ph = h * (PI_H_EDGEWISE if edgewise else PI_H)
    pw = ph * pi_aspect
    if pw > w * PI_MAX_W:
        pw = w * PI_MAX_W
        ph = pw / pi_aspect
    px = x + w - pw - 4.0
    if geo.edge == "bottom":         # the card comes up from underneath, so
        py = y + h - ph - 2.0        # the board is pushed to the TOP
    elif geo.edge == "top":          # and the other way about
        py = y + 2.0
    else:
        py = y + (h - ph) / 2.0
    brect = board_rect(geo, (px, py, pw, ph))

    card_len, card_w = card_size(geo, brect, card_aspect)
    if card_w < h * CARD_MIN_W:
        card_w = h * CARD_MIN_W
        card_len = card_w * card_aspect

    medic = None
    mw = 0.0
    mx = x + 2.0
    if medic_aspect:
        mh = h * MEDIC_H
        mw = mh * medic_aspect
        medic = (mx, y + (h - mh) / 2.0, mw, mh)

    # the reader is scaled so ITS slot matches the card's width
    rh = card_w * 1.15 / max(0.05, reader_slot_frac)
    rw = rh * reader_aspect
    rx = mx + mw * 0.86 if medic else x + 8.0
    ry = y + (h - rh) / 2.0
    seat = (rx + READER_SEAT[0] * rw, ry + (1.0 - READER_SEAT[1]) * rh)
    return Layout(medic=medic, reader=(rx, ry, rw, rh), pi=(px, py, pw, ph),
                  board=brect, card_len=card_len, card_w=card_w,
                  reader_seat=seat)


def card_corners(frame: Frame, card_len: float, card_w: float):
    """The four corners of the card at *frame*, rotation included."""
    a = math.radians(frame.angle)
    ca, sa = math.cos(a), math.sin(a)
    hx, hy = card_len / 2.0, card_w / 2.0
    return [(frame.cx + dx * ca - dy * sa, frame.cy + dx * sa + dy * ca)
            for dx, dy in ((-hx, -hy), (hx, -hy), (hx, hy), (-hx, hy))]


def path_bounds(geo, lay: Layout, samples: int = 90):
    """``(x0, y0, x1, y1)`` covering everywhere the card goes in one loop.

    Exists for a test rather than for the drawing, and it is the test that
    matters most here: Kivy does not clip a widget's canvas, so a card whose
    run-up starts below the stage is not invisible — it is painted across the
    body text under it.
    """
    xs, ys = [], []
    for i in range(samples + 1):
        f = handover_frame(i / float(samples), geo, lay.board, lay.card_len,
                           lay.reader_seat, READER_AXIS_DEG)
        for cx, cy in card_corners(f, lay.card_len, lay.card_w):
            xs.append(cx)
            ys.append(cy)
    return (min(xs), min(ys), max(xs), max(ys))


# --------------------------------------------------------------------------- #
# reading the measured document, when it lands
# --------------------------------------------------------------------------- #

_EDGE_WORDS = {"left": "left", "right": "right", "top": "top", "bottom": "bottom",
               "front": "bottom", "back": "top"}
_FIELDS = {
    "edge": ("edge", "side"),
    # "v" is what the measurement doc's column is actually called ("`v` (from
    # GPIO edge)"). Without it that column matched nothing, so the doc silently
    # failed to override and the built-in stood — 0.43 against a measured 0.41,
    # with nothing anywhere saying they disagreed (2026-08-07).
    "along": ("along", "position", "distance", "offset", "v", "v ("),
    "mouth": ("mouth", "width", "span"),
    "inset": ("inset", "depth", "setback"),
    "face": ("face", "mounted", "surface"),
    "retention": ("retention", "type", "mechanism", "fit"),
    "card_face_up": ("orientation", "label", "faceup", "face_up", "way"),
}


def _num(cell: str) -> Optional[float]:
    """A fraction from a doc cell: 0.38, 38%, "38 % along" all mean the same."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", cell)
    if m:
        return float(m.group(1)) / 100.0
    m = re.search(r"(?<![\w.])(0?\.\d+|[01](?:\.0+)?)(?![\w.])", cell)
    return float(m.group(1)) if m else None


def _column_map(header) -> Dict[str, int]:
    out = {}
    for i, cell in enumerate(header):
        c = cell.lower()
        for field, words in _FIELDS.items():
            if field in out:
                continue
            if any(w in c for w in words):
                out[field] = i
    return out


def parse_doc(text: str) -> Dict[str, dict]:
    """``{pi_key: {field: value}}`` for everything a markdown table states.

    Deliberately tolerant about wording and deliberately strict about values:
    a cell it cannot read confidently is SKIPPED, leaving the placeholder (and
    its ``provisional`` flag) in place. Half-understanding somebody else's
    table is how you end up with a card entering the wrong edge while the code
    reports itself as measured.
    """
    found: Dict[str, dict] = {}
    cols: Dict[str, int] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            cols = {}                        # a table ended
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue                         # the |---|---| rule row
        if not cols:
            cols = _column_map(cells)
            continue
        key = ""
        for cell in cells:
            m = re.search(r"\bpi_[a-z0-9_]+\b", cell.lower())
            if m:
                key = m.group(0)
                break
        if not key:
            continue
        rec: dict = {}
        for field, i in cols.items():
            if i >= len(cells):
                continue
            cell = cells[i].strip()
            low = cell.lower()
            if field == "edge":
                for word, edge in _EDGE_WORDS.items():
                    if re.search(rf"\b{word}\b", low):
                        rec["edge"] = edge
                        break
            elif field == "face":
                if "under" in low or "bottom" in low or "back" in low:
                    rec["face"] = "underside"
                elif "top" in low or "upper" in low or "front" in low:
                    rec["face"] = "top"
            elif field == "retention":
                # NEGATION FIRST. The doc says "friction — no click", and the
                # old order matched the bare word "click" and concluded the
                # socket clicks — reading an explicit DENIAL as an affirmation,
                # then overriding a correct built-in with the opposite of the
                # truth. Every board came out push_push while every default and
                # the document itself said friction (2026-08-07).
                denied = ("no click" in low or "not push" in low
                          or "no spring" in low or "doesn't click" in low
                          or "does not click" in low)
                if "friction" in low or "pull" in low or denied:
                    rec["retention"] = "friction"
                elif (low.count("push") >= 2 or "spring" in low
                        or "click" in low):
                    rec["retention"] = "push_push"
            elif field == "card_face_up":
                if "down" in low or "away" in low or "contacts up" in low:
                    rec["card_face_up"] = False
                elif "up" in low or "toward" in low or "towards" in low:
                    rec["card_face_up"] = True
            else:
                v = _num(cell)
                if v is not None:
                    rec[field] = v
        if rec:
            found.setdefault(key, {}).update(rec)
    return found


def _validate(field: str, value):
    """None for anything out of range — a bad cell must not become geometry."""
    if field == "edge":
        return value if value in EDGES else None
    if field == "face":
        return value if value in FACES else None
    if field == "retention":
        return value if value in RETENTIONS else None
    if field == "card_face_up":
        return bool(value)
    if field in ("along", "mouth", "inset"):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        return v if 0.0 <= v <= 1.0 else None
    return None


def apply_doc(base: Dict[str, SlotGeometry], text: str) -> Dict[str, SlotGeometry]:
    """*base* with everything the doc validly states written over it.

    A model the doc covers stops being provisional; a model it doesn't stays
    exactly as it was, still flagged. Pure, so the merge is testable without a
    file on disk.
    """
    out = dict(base)
    for key, rec in parse_doc(text).items():
        geo = out.get(ALIASES.get(key, key))
        if geo is None:
            continue
        good = {}
        for field, value in rec.items():
            v = _validate(field, value)
            if v is not None:
                good[field] = v
        if good:
            out[ALIASES.get(key, key)] = replace(
                geo, provisional=False,
                note=(geo.note + " Measured values from "
                      "docs/pi_sd_slot_geometry.md.").strip(), **good)
    return out


def _load() -> Dict[str, SlotGeometry]:
    """PI_SLOTS with the measured document applied, if it has appeared."""
    try:
        with open(DOC_PATH, encoding="utf-8") as fh:
            return apply_doc(PI_SLOTS, fh.read())
    except Exception:                        # absent, unreadable, malformed
        return dict(PI_SLOTS)


_WITH_DOC = _load()


def reload_doc() -> None:
    """Re-read the document — for a test, or for a session that outlives it."""
    global _WITH_DOC
    _WITH_DOC = _load()
