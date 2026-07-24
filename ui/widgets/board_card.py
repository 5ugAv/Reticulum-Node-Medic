"""A board photo with the node's name rendered live on its on-board screen.

For a board with an OLED (Heltec V3/V4) the name is overlaid in bold white right
on the little screen, exactly where it will appear once the node boots. For a
screenless board the overlay is suppressed (the caller shows the name in a band
above instead). Optionally tappable, so it doubles as a V3/V4 chooser tile.
"""

from __future__ import annotations

from kivy.graphics import Color, Line
from kivy.metrics import dp
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

from ui import board_images


class BoardCard(FloatLayout):
    """Photo of *board_key* with *name* drawn on its screen. ``on_select`` (if
    given) makes the whole card a button; ``selected`` draws an accent frame."""

    def __init__(self, board_key, name="", on_select=None, selected=False,
                 **kwargs):
        super().__init__(**kwargs)
        self._meta = board_images.get(board_key) or {}
        self._on_select = on_select
        self._selected = selected
        self._img = Image(source=self._meta.get("image", ""), allow_stretch=True,
                          keep_ratio=True, size_hint=(1, 1),
                          pos_hint={"x": 0, "y": 0})
        self.add_widget(self._img)
        # The on-screen name. White + bold so it reads as pixels lit on the OLED.
        self._name_lbl = Label(text=name or "", bold=True, color=(1, 1, 1, 1),
                               halign="center", valign="middle",
                               size_hint=(None, None), opacity=0)
        self.add_widget(self._name_lbl)
        with self.canvas.after:
            self._frame_col = Color(0, 0, 0, 0)
            self._frame = Line(width=dp(2))
        # Re-place the name whenever the photo's drawn rect can change: its own
        # pos/size (set by the layout) AND norm_image_size (set when the texture
        # loads / the letterbox recomputes). Defer the first pass to next frame so
        # the layout has actually run.
        self._img.bind(pos=self._reposition, size=self._reposition,
                       norm_image_size=self._reposition)
        self.bind(size=self._reposition, pos=self._reposition)
        # Nested BoxLayouts settle over several frames and the image texture loads
        # async, so a single deferred pass can run against a mid-layout position and
        # never re-fire. Re-place a few times as things settle (cheap — just moves a
        # label), then stop; also re-place on any later window resize.
        from kivy.clock import Clock
        from kivy.core.window import Window
        self._settle = Clock.schedule_interval(self._reposition, 1 / 15.0)
        Clock.schedule_once(lambda dt: self._settle and self._settle.cancel(), 1.2)
        Window.bind(size=self._reposition)

    # -- public -------------------------------------------------------------
    def set_name(self, text):
        self._name_lbl.text = text or ""
        self._reposition()

    def set_selected(self, on):
        self._selected = bool(on)
        self._reposition()

    # -- layout -------------------------------------------------------------
    def _reposition(self, *_):
        from ui import theme
        # accent frame when selected
        self._frame_col.rgba = (theme.hex_to_rgba(theme.COLORS["accent"])
                                if self._selected else (0, 0, 0, 0))
        self._frame.rounded_rectangle = (self.x, self.y, self.width, self.height,
                                         dp(6))
        oled = self._meta.get("oled")
        iw, ih = self._img.norm_image_size
        if not oled or not self._meta.get("has_screen") or iw < 2 or ih < 2 \
                or not self._name_lbl.text:
            self._name_lbl.opacity = 0
            return
        # the photo is letterboxed inside the Image widget; find its drawn rect
        ix = self._img.center_x - iw / 2.0
        iy = self._img.center_y - ih / 2.0
        x0, y0, x1, y1 = oled
        w = max(dp(4), (x1 - x0) * iw)
        h = max(dp(4), (y1 - y0) * ih)
        self._name_lbl.size = (w, h)
        self._name_lbl.text_size = (w, h)
        self._name_lbl.pos = (ix + x0 * iw, iy + ih - y1 * ih)   # y is from the top
        self._name_lbl.font_size = max(dp(9), h * 0.42)
        self._name_lbl.opacity = 1

    def on_touch_down(self, touch):
        if self._on_select and self.collide_point(*touch.pos):
            self._on_select()
            return True
        return super().on_touch_down(touch)
