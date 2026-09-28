"""Everything you press looks pressable — one rule, applied once.

Operator, 2026-09-28, looking at the BIRTH chooser on the panel: "Anywhere
there's something to be pressed, let's make sure the corners are rounded so it
looks like a button."

There are 196 ``Button(...)`` constructions across 38 files. Rounding them one
at a time would be 196 edits, every one a chance to miss, and the 197th button
would be square again. So the rule is applied to the CLASS: a Button installs a
rounded background when it is built, and every call site keeps working
unchanged — including the ones written after this.

HOW. Kivy paints a Button's background as a ``BorderImage`` in ``canvas.before``,
tinted by ``background_color``; with no source it renders a hard-cornered quad.
That instruction is REMOVED rather than hidden, and a ``RoundedRectangle`` takes
its place. ``background_color`` stays the single source of truth — code that
re-tints a button later still works, and the rounded shape follows it — which is
why this is a swap rather than a transparent overlay.

WHAT IS LEFT ALONE. A button whose background is a real image (the front page's
gear) keeps it: that artwork carries its own shape, and painting a rounded plate
behind a transparent PNG would put a box around an icon that was drawn without
one. Buttons still on Kivy's default theme atlas — ten of them, the ones nobody
styled — are rounded too and given the theme's surface colour, because a
default-grey rectangle among rounded cards is exactly the odd one out the
operator was pointing at.

THE PRESS. Removing the atlas also removes Kivy's own pressed look, so the fill
darkens on ``state == 'down'`` instead. That is deliberate: the same feedback
the five front-page keys got, now on every button in the tool.
"""

from __future__ import annotations

#: Corner radius in dp. One number for the whole tool — buttons that disagree
#: about their corners read as belonging to different applications.
RADIUS_DP = 10

#: How far the fill drops while the finger is down, and how far it fades when
#: the button is disabled.
PRESS_DARKEN = 0.72
DISABLED_ALPHA = 0.45

#: Kivy's own theme atlas. A button still pointing at it was never styled.
_DEFAULT_ATLAS = "atlas://data/images/defaulttheme/"

_patched = False


def is_flat(background_normal: str) -> bool:
    """Should this button's square background be replaced by a rounded one?

    True for the unstyled default and for ``background_normal=""`` (185 of the
    196 call sites). False for a real image path — that button was given
    artwork, and artwork brings its own shape.
    """
    src = (background_normal or "").strip()
    return not src or src.startswith(_DEFAULT_ATLAS)


def corner_radius(width: float, height: float, radius_px: float) -> float:
    """The corner radius for a button that size, in PIXELS.

    Never more than half the shorter side: a 40 px-wide "+" button with a 10 px
    radius is a button, the same radius on a 14 px one is a lozenge.

    Takes pixels rather than dp so it stays importable without a Window —
    ``kivy.metrics.dp`` asks for one, and this suite has no display. The dp
    conversion happens once in ``_install``, where a Window exists by
    definition.
    """
    if width <= 0 or height <= 0:
        return 0.0
    return max(0.0, min(radius_px, min(width, height) / 2.0))


def press_tint(rgba, state: str = "normal", disabled: bool = False):
    """The colour a flat button paints, given its state. Pure, so it can be
    tested without a display — no Kivy screen can be built in this suite."""
    r, g, b, a = rgba
    if disabled:
        return (r, g, b, a * DISABLED_ALPHA)
    if state == "down":
        return (r * PRESS_DARKEN, g * PRESS_DARKEN, b * PRESS_DARKEN, a)
    return (r, g, b, a)


def _install(btn, radius_dp: float) -> None:
    from kivy.graphics import BorderImage, Color, RoundedRectangle
    from kivy.metrics import dp
    from ui import theme

    radius_px = dp(radius_dp)

    before = btn.canvas.before
    for instr in list(before.children):
        if isinstance(instr, BorderImage):
            before.remove(instr)

    # An unstyled button carries Kivy's default white tint over the atlas it
    # has just lost. White is not a colour in this palette, so give it the
    # surface the styled cards use.
    if (btn.background_normal or "").startswith(_DEFAULT_ATLAS) \
            and tuple(btn.background_color) == (1, 1, 1, 1):
        btn.background_color = theme.hex_to_rgba(theme.COLORS["surface"])
    btn.background_normal = ""
    btn.background_down = ""

    with before:
        btn._rb_color = Color(*press_tint(btn.background_color, btn.state, btn.disabled))
        btn._rb_rect = RoundedRectangle(
            pos=btn.pos, size=btn.size,
            radius=[corner_radius(btn.width, btn.height, radius_px)] * 4)

    def repaint(*_):
        btn._rb_color.rgba = press_tint(btn.background_color, btn.state, btn.disabled)
        btn._rb_rect.pos = btn.pos
        btn._rb_rect.size = btn.size
        btn._rb_rect.radius = [corner_radius(btn.width, btn.height, radius_px)] * 4

    btn.bind(pos=repaint, size=repaint, state=repaint, disabled=repaint,
             background_color=repaint)
    btn._rb_repaint = repaint


def enable(radius_dp: float = RADIUS_DP) -> bool:
    """Round every button the tool builds from here on. Idempotent.

    Returns True the first time, False if it was already on — so a second call
    from another entry point cannot double-wrap ``__init__``.
    """
    global _patched
    if _patched:
        return False
    from kivy.uix.button import Button        # ToggleButton subclasses it

    original = Button.__init__

    def __init__(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if is_flat(self.background_normal):
            try:
                _install(self, radius_dp)
            except Exception:          # noqa: BLE001
                pass                   # a square corner is a blemish; a raised
                                       # exception here is a tool that won't open
    Button.__init__ = __init__
    _patched = True
    return True
