"""The live Wi-Fi icon on the front page: covers the poster's painted Wi-Fi
symbol and draws four phone-style bars from ui.wifi_level. Grey when the
radio is off; dim when on but not joined; lit by strength when joined. Tapping
it is the poster's own Wi-Fi zone (home_zones), which opens Wi-Fi settings."""
from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.widget import Widget

from ui import theme
from ui.wifi_level import CONNECTED, OFF, bar_geometry, bars_for, state_for

#: The poster's own dark behind the symbol (measured off front_page.png).
_POSTER_DARK = (0 / 255, 16 / 255, 0 / 255, 1)
_GREY = (0.52, 0.56, 0.52, 1)
_GREY_DIM = (0.52, 0.56, 0.52, 0.35)


class WifiIcon(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.size = (dp(34), dp(34))
        self._summary = {"enabled": True, "connected": False, "signal": None}
        self.bind(pos=self._redraw, size=self._redraw)
        self._redraw()

    def update(self, summary: dict) -> None:
        """Feed provisioning.wifi.wifi_summary()."""
        self._summary = dict(summary or {})
        self._redraw()

    def _redraw(self, *_):
        from kivy.graphics import Color, Ellipse, Rectangle
        s = self._summary
        state = state_for(bool(s.get("enabled", True)), bool(s.get("connected")))
        lit = bars_for(bool(s.get("enabled", True)), bool(s.get("connected")), s.get("signal"))
        self.canvas.before.clear()
        x, y, w, h = self.x, self.y, self.width, self.height
        green = theme.hex_to_rgba(theme.COLORS["green"])
        dim = theme.hex_to_rgba(theme.COLORS["green"], 0.28)
        with self.canvas.before:
            # the disc that hides the painted symbol, in the poster's own dark
            Color(*_POSTER_DARK)
            Ellipse(pos=(x - w * 0.18, y - h * 0.18), size=(w * 1.36, h * 1.36))
            # the same insets as scripts/preview_wifi_icon.py: what was looked
            # at is what ships
            bx, by, bw, bh = x + w * 0.18, y + h * 0.2, w * 0.64, h * 0.6
            for i, (rx, ry, rw, rh) in enumerate(bar_geometry(bw, bh)):
                if state == OFF:
                    Color(*_GREY)
                elif state == CONNECTED and i < lit:
                    Color(*green)
                else:
                    Color(*(dim if state == CONNECTED else _GREY_DIM))
                Rectangle(pos=(bx + rx, by + ry), size=(rw, rh))
