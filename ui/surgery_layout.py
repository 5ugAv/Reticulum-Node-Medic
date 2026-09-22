"""Where everything in the operating theatre stands, and how the surgeon's arm
moves. Pure — no Kivy — so the widget and the offline previewer draw from ONE
set of numbers, the same split as ui.pi_sd_geometry and its previewer.

Why this file exists (2026-09-22). The operator photographed the card-writing
screen and said the medic's arms were "very rudimentary — just some pink
coloured lines that are supposed to represent arms holding the SD card,
throwing the icons back and forwards." Reading the widget confirmed it:

  * the "arms" were two 3 px straight strokes in a skin tone (0.98, 0.82,
    0.64) from the medic's lower-left corner to one point on the card, with a
    10 px disc for a hand — string, not limbs, and PINK on a red/cream/green
    theme where the medic has no skin at all;
  * the organ in flight did not travel from the hand: it interpolated from a
    fixed point in mid-air (0.72, 0.52 of the widget) straight to its seat on
    a raised cosine, once per 1.67 s loop — thrown, never held;
  * the card (a fingernail of plastic) was drawn 1.6x wider than the medic (a
    hand-held device), so the one doing the work read as a small object
    standing nearby.

So the medic now has ONE proper instrument arm — shoulder on its case, a fixed
upper arm, a telescoping forearm, a pair of forceps — that goes through a real
pick-and-place cycle: the organ is presented on an instrument tray beside the
patient, gripped, carried on an arc, fitted into its seat, released, and the
arm withdraws for the next. That is the operator's own pose sheet for the
retired Dr. Pi surgeon (assets/ui/anim/drpi/POSES.md: raise, reach out, lower,
place, withdraw), done with the medic instead. The arm is the medic's own case
colour with silver forceps; nothing is pink.

Everything is a fraction of the box the widget is given, so it fits the 5"
panel's stage and a narrower one alike with nothing clipped (the 2026-08-12
lesson: never a fixed height).
"""

from __future__ import annotations

import math
from typing import List, NamedTuple, Optional, Sequence, Tuple

from ui.organ_art import CARD_WINDOW, ORGAN_SEATS

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]          # x, y (UP), w, h

# --------------------------------------------------------------------------- #
# the stage, as fractions
# --------------------------------------------------------------------------- #

#: The heart monitor is a band across the top; the theatre is everything under
#: it. 0.34 of the height went to the monitor before; the scene under it was
#: what suffered, so the band is trimmed and the surgeon gets the room.
MON_H = 0.27
SCENE_GAP = 0.02
FLOOR = 0.03
#: The surgeon is the biggest thing on the stage, by height — it is the one
#: doing the work, and it is the device in the operator's hand.
MEDIC_H = 0.94
MEDIC_MAX_W = 0.40
MEDIC_RIGHT_PAD = 0.015
TABLE_LEFT = 0.04
TABLE_GAP = 0.02
TABLE_SURFACE = 0.16          # of scene height, above the floor
TABLE_THICK = 0.07
#: The patient is a cartoon patient: bigger than a real microSD would be next
#: to the medic, because five organs have to be readable inside it — but never
#: much WIDER than the surgeon, and never as tall. The photo had that
#: backwards (card 1.6x the medic's width).
CARD_OF_TABLE = 0.62
CARD_OF_MEDIC = 1.12
CARD_MAX_H = 0.72
#: The instrument tray on the table between patient and surgeon, where the
#: next organ is presented. Its size, as fractions of the card's.
TRAY_W = 0.30
TRAY_H = 0.16
#: Arm anchors, as fractions of the medic's box. The shoulder is LOW on the
#: case's left edge: the elbow rises from there and the forearm slopes down
#: to the table, the silhouette of someone reaching over a patient — and a
#: low shoulder is what gives the elbow room under the monitor. The park pose
#: is "tweezers raised high" (drpi pose_02).
SHOULDER = (0.05, 0.24)
PARK = (-0.36, 0.98)
#: The upper arm is bounded three ways: it may not reach the monitor even
#: pointing straight up, it must leave room for a forearm when the tip is at
#: the tray, and it is never more than a third of the longest reach.
UPPER_OF_PICK = 0.70
UPPER_OF_FAR = 0.30
UPPER_CEILING_PAD = 0.06      # of stage height, kept clear under the monitor
#: How far the elbow lifts off the shoulder-to-tip line, fully extended.
BEND_DEG = 38.0
#: Forceps length, in organ radii; drawn organ diameter, in organ radii.
FORCEPS_R = 2.6
ORGAN_DIAM_R = 2.3

# the pick-and-place cycle, as fractions of one loop of the free phase
PICK_END = 0.14
GRIP_AT = 0.07
CARRY_END = 0.56
PLACE_END = 0.74
RELEASE_AT = 0.65
#: The free phase advances this much per second (2.4 s per cycle). It was 0.6,
#: a 1.67 s loop, which is fine for a thrown disc and frantic for a placement.
PHASE_PER_S = 0.42
#: Trace scroll per phase, chosen so the heart rate did not change when the
#: loop slowed: 0.6 * 2.0 == 0.42 * 2.86, about 72 beats a minute.
TRACE_SCROLL = 2.86


class Layout(NamedTuple):
    stage: Rect
    monitor: Rect
    table: Rect              # the slab (surface is its top edge)
    pedestal: Rect
    base: Rect
    card: Rect
    window: Rect
    tray: Rect
    medic: Rect
    shoulder: Point
    pick: Point
    park: Point
    upper: float             # upper-arm length (fixed)
    forceps: float
    organ_r: float
    ceiling: float           # nothing of the arm goes above this
    lead_pad: Point


class Pose(NamedTuple):
    tip: Point
    elbow: Point
    sleeve: Point            # where the forearm's outer sleeve ends
    sleeve2: Point           # and its second, thinner stage
    wrist: Point
    tine_a: Point
    tine_b: Point
    carrying: bool           # the organ is in the forceps
    fit: float               # 0..1: the organ drawn at its seat, being fitted
    open: float              # 0 closed .. 1 open


def _ease(v: float) -> float:
    v = 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
    return v * v * (3.0 - 2.0 * v)


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


# --------------------------------------------------------------------------- #
# layout
# --------------------------------------------------------------------------- #

def layout(stage: Rect, card_aspect: float, medic_aspect: float,
           pad: float = 2.0) -> Layout:
    """Place monitor, table, patient, tray and surgeon in *stage* ``(x, y, w, h)``.

    *card_aspect* and *medic_aspect* are width/height of the sprites; *pad* is
    the hairline margin the monitor keeps from the box edge (dp(2) on the
    device, 2 px offline).
    """
    x, y, w, h = stage
    mon_h = h * MON_H - pad
    monitor = (x + pad, y + h - h * MON_H, w - 2 * pad, mon_h)
    floor = y + h * FLOOR
    scene_h = h * (1.0 - MON_H - SCENE_GAP - FLOOR)

    mh = scene_h * MEDIC_H
    mw = mh * medic_aspect
    if mw > w * MEDIC_MAX_W:
        mw = w * MEDIC_MAX_W
        mh = mw / medic_aspect
    mx = x + w - mw - w * MEDIC_RIGHT_PAD
    medic = (mx, floor, mw, mh)

    tx = x + w * TABLE_LEFT
    tw = max(1.0, mx - w * TABLE_GAP - tx)
    surface = floor + scene_h * TABLE_SURFACE
    thick = scene_h * TABLE_THICK
    table = (tx, surface - thick, tw, thick)
    pw = tw * 0.16
    pedestal = (tx + (tw - pw) / 2.0, floor, pw, surface - thick - floor)
    bw = tw * 0.42
    base = (tx + (tw - bw) / 2.0, floor, bw, scene_h * 0.045)

    cw = min(tw * CARD_OF_TABLE, mw * CARD_OF_MEDIC)
    ch = cw / card_aspect
    if ch > scene_h * CARD_MAX_H:
        ch = scene_h * CARD_MAX_H
        cw = ch * card_aspect
    trw, trh = cw * TRAY_W, ch * TRAY_H
    # card and tray share the table: the card centred in what is left of it
    # once the tray has taken its share, the tray midway between the card
    # and the table's end — close enough to the surgeon to be its own, far
    # enough that the arm is never folded when it reaches for the tray
    cx = tx + (tw - trw - cw) / 2.0
    cx = max(tx, min(cx, tx + tw - trw - cw))
    card = (cx, surface, cw, ch)
    gap = max(0.0, tx + tw - (cx + cw) - trw)
    tray = (cx + cw + gap * 0.5, surface, trw, trh)

    wx0, wy0, wx1, wy1 = CARD_WINDOW                # fractions, y DOWN
    window = (cx + cw * wx0, surface + ch * (1.0 - wy1),
              cw * (wx1 - wx0), ch * (wy1 - wy0))
    organ_r = min(window[2] / 14.0, window[3] * 0.38)
    forceps = organ_r * FORCEPS_R

    shoulder = (mx + mw * SHOULDER[0], floor + mh * SHOULDER[1])
    pick = (tray[0] + trw / 2.0, surface + trh + organ_r * 0.9)
    ceiling = monitor[1] - h * UPPER_CEILING_PAD
    park = (mx + mw * PARK[0], min(floor + mh * PARK[1], ceiling - organ_r))
    far = max([_dist(shoulder, seat_point(window, key)) for key in ORGAN_SEATS]
              + [_dist(shoulder, pick), _dist(shoulder, park)])
    upper = max(1.0, min(ceiling - shoulder[1] - organ_r,
                         _dist(shoulder, pick) * UPPER_OF_PICK,
                         far * UPPER_OF_FAR))

    lead_pad = (cx + cw * 0.80, surface + ch * 0.985)
    return Layout(stage=stage, monitor=monitor, table=table, pedestal=pedestal,
                  base=base, card=card, window=window, tray=tray, medic=medic,
                  shoulder=shoulder, pick=pick, park=park, upper=upper,
                  forceps=forceps, organ_r=organ_r, ceiling=ceiling,
                  lead_pad=lead_pad)


def organ_diam(lay: Layout) -> float:
    """The drawn size of an organ, seated or carried."""
    return lay.organ_r * ORGAN_DIAM_R


def seat_point(window: Rect, key: str) -> Point:
    """Where organ *key* sits, in stage coordinates (y UP)."""
    fx, fy = ORGAN_SEATS.get(key, (0.5, 0.5))
    return (window[0] + window[2] * fx, window[1] + window[3] * fy)


def lead_points(lay: Layout, n: int = 24) -> List[Point]:
    """The monitor's lead: from the band down to the pad on the patient. What
    ties the trace to the card — before, the monitor was a strip of UI above
    an unrelated scene."""
    px, py = lay.lead_pad
    mx, my, mw, mh = lay.monitor
    top = (min(mx + mw - 4.0, px + lay.card[2] * 0.22), my)
    c1 = (top[0] + lay.card[2] * 0.10, my - (my - py) * 0.45)
    c2 = (px + lay.card[2] * 0.14, py + (my - py) * 0.30)
    pts = []
    for i in range(n + 1):
        s = i / float(n)
        m = 1.0 - s
        pts.append((m ** 3 * top[0] + 3 * m * m * s * c1[0]
                    + 3 * m * s * s * c2[0] + s ** 3 * px,
                    m ** 3 * top[1] + 3 * m * m * s * c1[1]
                    + 3 * m * s * s * c2[1] + s ** 3 * py))
    return pts


# --------------------------------------------------------------------------- #
# the arm
# --------------------------------------------------------------------------- #

def _elbow(lay: Layout, tip: Point) -> Point:
    """The elbow: a fixed upper arm from the shoulder, lifted off the line to
    the tip by up to BEND_DEG — less when the tip is close, so the forearm
    never has to fold back through the upper arm. Of the two mirror
    positions the HIGHER is taken (a surgeon's elbow is up, not dragging on
    the table), and it can never reach the monitor because the upper arm's
    length is bounded by the ceiling in layout().

    Why not a two-link arm with plain inverse kinematics: on a stage three
    times wider than it is tall, an arm long enough to reach the far seat
    folds when it comes back to the tray, and a folded two-link arm throws
    its elbow sqrt(L^2 - (d/2)^2) off the line — straight through the
    monitor. Measured before it was drawn, not after.
    """
    d = _dist(lay.shoulder, tip)
    if d < 1e-6:
        return (lay.shoulder[0], lay.shoulder[1] + lay.upper)
    ux, uy = (tip[0] - lay.shoulder[0]) / d, (tip[1] - lay.shoulder[1]) / d
    bend = math.radians(BEND_DEG) * _ease(min(1.0, d / (2.2 * lay.upper)))
    best = None
    for sign in (1.0, -1.0):
        ca, sa = math.cos(sign * bend), math.sin(sign * bend)
        ex = lay.shoulder[0] + (ux * ca - uy * sa) * lay.upper
        ey = lay.shoulder[1] + (ux * sa + uy * ca) * lay.upper
        if best is None or ey > best[1]:
            best = (ex, ey)
    return best


def arm_pose(u: float, lay: Layout, seat: Optional[Point]) -> Pose:
    """The arm at cycle position *u* (0..1) on its way to *seat*.

    ``seat`` None means there is nothing left to implant: the arm parks
    raised, forceps open — the finished pose.

    One cycle:  0 .. 0.14  over the tray, the organ appears and is gripped
                            (forceps close at 0.07);
               0.14 .. 0.56 carried on a lifted arc to the seat;
               0.56 .. 0.74 fitted — pressed home, then released at 0.65,
                            the forceps opening as they let go;
               0.74 .. 1.0  withdrawn to the tray for the next one.
    """
    u = u % 1.0
    r = lay.organ_r
    if seat is None:
        tip, carrying, fit, opn = lay.park, False, 0.0, 1.0
    elif u < PICK_END:
        tip = lay.pick
        carrying = u >= GRIP_AT
        opn = 1.0 - _ease(u / GRIP_AT)
        fit = 0.0
    elif u < CARRY_END:
        p = _ease((u - PICK_END) / (CARRY_END - PICK_END))
        mid = _lerp(lay.pick, seat, 0.5)
        lift = _dist(lay.pick, seat) * 0.45 + r * 2.0
        top = min(max(lay.pick[1], seat[1]) + lift, lay.ceiling - r * 2.0)
        ctrl = (mid[0], top)
        m = 1.0 - p
        tip = (m * m * lay.pick[0] + 2 * m * p * ctrl[0] + p * p * seat[0],
               m * m * lay.pick[1] + 2 * m * p * ctrl[1] + p * p * seat[1])
        carrying, fit, opn = True, 0.0, 0.0
    elif u < PLACE_END:
        q = (u - CARRY_END) / (PLACE_END - CARRY_END)
        tip = (seat[0], seat[1] - r * 0.18 * math.sin(math.pi * q))
        carrying = u < RELEASE_AT
        fit = _ease((u - CARRY_END) / (RELEASE_AT - CARRY_END))
        opn = _ease((u - RELEASE_AT) / (PLACE_END - RELEASE_AT)) if not carrying else 0.0
    else:
        p = _ease((u - PLACE_END) / (1.0 - PLACE_END))
        tip = _lerp(seat, lay.pick, p)
        tip = (tip[0], tip[1] + r * 1.6 * math.sin(math.pi * p))
        carrying, opn = False, 1.0
        fit = 1.0 - p

    elbow = _elbow(lay, tip)
    d = _dist(elbow, tip)
    ux, uy = ((tip[0] - elbow[0]) / d, (tip[1] - elbow[1]) / d) if d > 1e-6 else (0.0, 1.0)
    fore = max(0.0, d - lay.forceps)
    wrist = (elbow[0] + ux * fore, elbow[1] + uy * fore)
    # the forearm telescopes: two sleeves the length of the upper arm, then
    # the inner rod — so at full stretch it still reads as a limb in stages
    # rather than one long thin wire (first offline render, 2026-09-22)
    sleeve_len = min(fore, lay.upper * 0.9)
    sleeve2_len = min(fore, lay.upper * 1.8)
    sleeve = (elbow[0] + ux * sleeve_len, elbow[1] + uy * sleeve_len)
    sleeve2 = (elbow[0] + ux * sleeve2_len, elbow[1] + uy * sleeve2_len)
    gap = r * 1.0 if carrying else r * (0.30 + 1.30 * opn)
    px, py = -uy, ux                                   # perpendicular
    tine_a = (tip[0] + px * gap, tip[1] + py * gap)
    tine_b = (tip[0] - px * gap, tip[1] - py * gap)
    return Pose(tip=tip, elbow=elbow, sleeve=sleeve, sleeve2=sleeve2,
                wrist=wrist, tine_a=tine_a, tine_b=tine_b, carrying=carrying,
                fit=fit, open=opn)


#: The next organ fades in on the tray as the arm comes back for it.
TRAY_FADE_FROM = 0.86


def tray_organ_alpha(u: float) -> float:
    """How visible the WAITING organ is on the tray at cycle position *u*:
    there while the forceps close on it, gone once it is carried, back as the
    arm returns. Without this the first render showed forceps closing on
    thin air — the organ only came into being once it was held."""
    u = u % 1.0
    if u < GRIP_AT:
        return 1.0
    if u >= TRAY_FADE_FROM:
        return _ease((u - TRAY_FADE_FROM) / (1.0 - TRAY_FADE_FROM))
    return 0.0


# --------------------------------------------------------------------------- #
# the monitor
# --------------------------------------------------------------------------- #

def monitor_trace(rect: Rect, steady: float, phase: float,
                  n: int = 90) -> List[Point]:
    """The ECG: noise and dropouts early, a clean strong rhythm by the end.

    Moved here verbatim from the widget so the previewer draws the same trace.
    The amplitude is never driven to zero — a flat line means the patient
    died, the exact opposite of what this screen reports (see the tests).
    """
    x, y, w, h = rect
    mid = y + h * 0.5
    amp = h * (0.16 + 0.26 * steady)
    scroll = phase * TRACE_SCROLL
    pts = []
    for i in range(n + 1):
        u = i / float(n)
        beat = (u * 2.2 + scroll) % 1.0
        v = 0.0
        if 0.10 < beat < 0.16:                 # P
            v = 0.22
        elif 0.20 < beat < 0.24:               # Q
            v = -0.30
        elif 0.24 < beat < 0.30:               # R, the spike
            v = 1.0
        elif 0.30 < beat < 0.35:               # S
            v = -0.45
        elif 0.45 < beat < 0.56:               # T
            v = 0.30
        if steady < 0.9:
            flicker = math.sin((u * 13.0 + phase * 5.0) * math.pi)
            if flicker > (0.10 + 0.85 * steady):
                v *= 0.15
            v += (1.0 - steady) * 0.10 * math.sin(u * 47.0 + scroll * 6.0)
        pts.append((x + w * u, mid + amp * v))
    return pts


def rects(lay: Layout) -> Sequence[Tuple[str, Rect]]:
    """Every drawn box, named — for the tests that keep the scene inside its
    stage and the surgeon bigger than the patient."""
    return (("monitor", lay.monitor), ("table", lay.table),
            ("pedestal", lay.pedestal), ("base", lay.base), ("card", lay.card),
            ("window", lay.window), ("tray", lay.tray), ("medic", lay.medic))
