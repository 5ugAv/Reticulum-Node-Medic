"""Geometry for the slide-to-power control — pure, so it can be tested and
previewed without a Kivy window.

Two track styles (operator, 2026-09-22, on the front page): the ORIGINAL
"pill" — a rounded bar 64 % of the knob's height that carries a sentence
("slide to wipe  →") — and the "line": a thin black line the red knob rides
along, the knob itself unchanged. The line cannot carry a sentence, so the
front page's one-word hint sits ON the line in a small face; the pill keeps
its text inside.
"""

PILL_FRAC = 0.64        # pill height as a fraction of the widget (knob) height
LINE_FRAC = 0.06        # line thickness as a fraction of the knob height
LINE_MIN_PX = 2.0       # never thinner than this, whatever the density

STYLES = ("pill", "line")


def track_rect(x, y, w, h, style="pill"):
    """``(tx, ty, tw, th, radius)`` of the track inside a widget at (x, y)
    of size (w, h). Both styles are vertically centred on the knob."""
    if style not in STYLES:
        raise ValueError(style)
    th = h * PILL_FRAC if style == "pill" else max(LINE_MIN_PX, h * LINE_FRAC)
    ty = y + (h - th) / 2.0
    return x, ty, w, th, th / 2.0


def hint_font_px(h, style="pill", floor_px=9.5):
    """The hint's font size: half the pill's height, or a fixed small face
    on the line (the line's own thickness is no size for a word)."""
    if style == "line":
        return max(floor_px, h * 0.26)
    return max(floor_px, h * PILL_FRAC * 0.5)


def hint_rect(x, y, w, h, knob, style="pill"):
    """Where the hint label sits: right of the resting knob, centred on the
    track's centre line. On the line style it is a band as tall as the
    hint's face so the word reads on the line, not squashed into it."""
    off = knob * 0.35
    tx, ty, tw, th, _ = track_rect(x, y, w, h, style)
    if style == "line":
        band = hint_font_px(h, style) * 1.6
        return x + off, ty + th / 2.0 - band / 2.0, w - off, band
    return x + off, ty, w - off, th
