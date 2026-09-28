"""Geometry for the slide-to-power control — pure, so it can be tested and
previewed without a Kivy window.

Three track styles. The ORIGINAL "pill" — a rounded bar 64 % of the knob's
height that carries a sentence ("slide to wipe  →"). The "line" (operator,
2026-09-22) — a thin black line the knob rides along; it cannot carry a
sentence, so its one-word hint sits ON the line in a small face. And "neon"
(operator, 2026-09-29) — a full-height bordered capsule in the front page's
own green, from the artwork they supplied: the knob rides inside it rather
than proud of it, so the track has to be nearly as tall as the knob.
"""

PILL_FRAC = 0.64        # pill height as a fraction of the widget (knob) height
LINE_FRAC = 0.06        # line thickness as a fraction of the knob height
LINE_MIN_PX = 2.0       # never thinner than this, whatever the density
NEON_FRAC = 0.88        # the capsule the knob rides INSIDE, not proud of

# --- the painted capsule (assets/ui/power_track.png) -------------------------
# The operator supplied the track as artwork on 2026-09-29 — ON and OFF are
# PAINTED on it, so the neon style draws no capsule and no hint of its own. All
# five numbers below were MEASURED: the first four off the track art, the last
# two off the operator's own composite showing where the button belongs.
NEON_ART_ASPECT = 1924 / 567          # the PNG, glow margin included
NEON_PILL = (0.0135, 0.0459, 0.9865, 0.9541)   # the capsule inside that art
NEON_KNOB_H = 0.883                   # knob diameter, as a fraction of the pill's height
NEON_REST = 0.340                     # resting knob centre, across the pill
NEON_END = 0.6755                     # centre at full travel: its edge meets the
                                      # inner channel's end, short of the OFF text

STYLES = ("pill", "line", "neon")


def track_rect(x, y, w, h, style="pill"):
    """``(tx, ty, tw, th, radius)`` of the track inside a widget at (x, y)
    of size (w, h). Both styles are vertically centred on the knob."""
    if style not in STYLES:
        raise ValueError(style)
    if style == "pill":
        th = h * PILL_FRAC
    elif style == "neon":
        th = h * NEON_FRAC
    else:
        th = max(LINE_MIN_PX, h * LINE_FRAC)
    ty = y + (h - th) / 2.0
    return x, ty, w, th, th / 2.0


def hint_font_px(h, style="pill", floor_px=9.5):
    """The hint's font size: half the pill's height, or a fixed small face
    on the line (the line's own thickness is no size for a word)."""
    if style == "line":
        return max(floor_px, h * 0.26)
    if style == "neon":
        return max(floor_px, h * NEON_FRAC * 0.42)
    return max(floor_px, h * PILL_FRAC * 0.5)


def hint_rect(x, y, w, h, knob, style="pill"):
    """Where the hint label sits: right of the resting knob, centred on the
    track's centre line. On the line style it is a band as tall as the
    hint's face so the word reads on the line, not squashed into it."""
    off = knob * 0.35
    tx, ty, tw, th, _ = track_rect(x, y, w, h, style)
    if style == "neon":
        # OFF sits between the resting knob and the capsule's far cap, INSIDE
        # the capsule: it is the place the knob has to reach, so it must not
        # spill past the end of the thing the knob travels along. The right
        # margin is half the cap's radius, which keeps the word clear of the
        # curve rather than touching it.
        return x + knob, ty, max(1.0, w - knob - th * 0.28), th
    if style == "line":
        band = hint_font_px(h, style) * 1.6
        return x + off, ty + th / 2.0 - band / 2.0, w - off, band
    return x + off, ty, w - off, th


def neon_pill_rect(x, y, w, h):
    """The painted capsule's own rect inside a widget showing the track art.

    The art carries a glow margin, so the capsule is smaller than the widget.
    Everything that must line up with the PICTURE — the knob, its travel — is
    measured against this, never against the widget.
    """
    x0, y0, x1, y1 = NEON_PILL
    return x + x0 * w, y + (1.0 - y1) * h, (x1 - x0) * w, (y1 - y0) * h


def neon_knob_rect(x, y, w, h, progress=0.0):
    """``(kx, ky, size)`` of the knob on the painted track at *progress* 0..1.

    It rides the inner channel: resting over ON, ending where the channel does,
    short of the painted OFF. Square, because the knob art is.
    """
    px, py, pw, ph = neon_pill_rect(x, y, w, h)
    size = ph * NEON_KNOB_H
    cx = px + pw * (NEON_REST + (NEON_END - NEON_REST) * max(0.0, min(1.0, progress)))
    return cx - size / 2.0, py + (ph - size) / 2.0, size


def neon_progress(x, y, w, h, knob_cx):
    """The inverse: how far along its travel a knob centred at *knob_cx* is."""
    px, _py, pw, _ph = neon_pill_rect(x, y, w, h)
    span = pw * (NEON_END - NEON_REST)
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (knob_cx - (px + pw * NEON_REST)) / span))
