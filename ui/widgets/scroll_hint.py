"""A visible "there is more below" marker for scrolling screens.

The operator, walking the birth flow (2026-08-02): *"any page that has to
scroll down to see the continue button needs some sort of arrow at the bottom
... otherwise it just looks hidden, and unless you know what's going on you're
going to get lost here."*

Exactly right, and it is worse on a touchscreen with no mouse wheel, no visible
scrollbar thumb until you drag, and content that ends mid-field so there is no
cut-off row to hint at more. A green button below the fold reads as an ABSENT
button, not a hidden one — the operator concludes the screen is broken or that
they missed a step.

So: a chevron pinned to the bottom of the scroll area that shows only while
there is something below, and fades out once you reach the end. It is
non-interactive on purpose — it marks, it does not act, so it can never eat a
touch meant for the content under it.
"""

from __future__ import annotations

from kivy.animation import Animation
from kivy.graphics import Color, Line
from kivy.metrics import dp
from kivy.properties import NumericProperty
from kivy.uix.widget import Widget

from ui import theme
from ui.scroll_rule import more_below


class ScrollHint(Widget):
    """Chevron marking more content below. Attach with :func:`attach`."""

    glow = NumericProperty(0.0)

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(46), dp(26)))
        super().__init__(**kwargs)
        self._shown = False
        self.opacity = 0.0
        self.bind(pos=self._redraw, size=self._redraw, glow=self._redraw)
        self._redraw()

    def _redraw(self, *_a):
        self.canvas.clear()
        w, h = self.width, self.height
        cx = self.x + w / 2.0
        top = self.y + h * 0.72
        bot = self.y + h * 0.28
        arm = w * 0.30
        with self.canvas:
            # a soft halo so it reads against both the dark panel and a field
            Color(*theme.hex_to_rgba(theme.COLORS["background"], 0.55))
            Line(points=[cx - arm, top, cx, bot, cx + arm, top],
                 width=dp(5.0), cap="round", joint="round")
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            Line(points=[cx - arm, top, cx, bot, cx + arm, top],
                 width=dp(2.4), cap="round", joint="round")

    # -- visibility --------------------------------------------------------

    def show(self):
        if self._shown:
            return
        self._shown = True
        Animation.cancel_all(self, "opacity")
        Animation(opacity=1.0, duration=0.25).start(self)

    def hide(self):
        if not self._shown:
            return
        self._shown = False
        Animation.cancel_all(self, "opacity")
        Animation(opacity=0.0, duration=0.25).start(self)


def attach(scrollview, parent=None, hint=None):
    """Put a :class:`ScrollHint` on *scrollview* and keep it in sync.

    Returns the hint. Safe to call on any ScrollView; if anything about the
    layout is unusual the hint simply stays hidden rather than misleading.
    """
    from kivy.clock import Clock

    hint = hint or ScrollHint()
    host = parent if parent is not None else scrollview

    def _place(*_a):
        hint.center_x = scrollview.center_x
        hint.y = scrollview.y + dp(6)

    def _sync(*_a):
        try:
            content = scrollview.children[0] if scrollview.children else None
            ch = getattr(content, "height", 0) or 0
            if more_below(scrollview.scroll_y, scrollview.height, ch):
                hint.show()
            else:
                hint.hide()
        except Exception:                              # never break a screen
            hint.hide()
        _place()

    scrollview.bind(scroll_y=_sync, size=_sync, pos=_sync)
    try:
        host.add_widget(hint)
    except Exception:
        return hint
    Clock.schedule_once(_sync, 0.1)
    Clock.schedule_interval(_sync, 0.5)                # content can grow later
    return hint
