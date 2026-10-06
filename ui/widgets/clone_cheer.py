"""The "you made a Node Medic" moment (keeper, 2026-10-06).

The clone used to end in small text at the bottom of the step list, which read
as if nothing had changed. The keeper asked for "a big pop-up ... congratulations,
like a bit of a dopamine kick, a different color, a word bubble", saying what
to do next: unplug the cable, and follow the new medic, which sets up its
Heltec Wireless Tracker as its LoRa radio and GPS.

A ModalView: a lime speech bubble (the theme's accent, dark text) that fades
in over a dimmed page, joy rings blooming behind it, and one button to close it.
"""
from __future__ import annotations

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, RoundedRectangle, Triangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView

from ui import theme
from ui.i18n import tr
from ui.text_fit import grow_to_text

INK = "#041004"          # the theme's background green-black, as text on lime


def _words(text, size, bold=False, markup=False):
    lbl = Label(text=text, bold=bold, markup=markup, halign="center",
                valign="middle", font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(INK))
    return grow_to_text(lbl)


class _Bubble(BoxLayout):
    """A rounded lime panel with a little tail at the bottom: a word bubble."""

    TAIL = 22

    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=[dp(22), dp(20), dp(22), dp(20)],
                         spacing=dp(12), size_hint_y=None, **kw)
        self.bind(minimum_height=self.setter("height"))
        with self.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["accent"]))
            self._bg = RoundedRectangle(radius=[dp(26)])
            self._tail = Triangle()
        self.bind(pos=self._paint, size=self._paint)

    def _paint(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size
        t = dp(self.TAIL)
        x, y = self.x + self.width * 0.22, self.y
        self._tail.points = [x, y + 1, x + t * 1.4, y + 1, x + t * 0.2, y - t]


def show_clone_cheer(new_name: str = "", on_close=None) -> ModalView:
    view = ModalView(size_hint=(0.92, None), auto_dismiss=False,
                     background="", background_color=(0, 0, 0, 0),
                     overlay_color=(0, 0, 0, 0.72))
    root = FloatLayout(size_hint_y=None)
    view.add_widget(root)

    # joy rings behind the bubble
    try:
        from ui.screens.firstborn_screen import _JoyBurst
        burst = _JoyBurst(size_hint=(1, 1), pos_hint={"x": 0, "y": 0})
        root.add_widget(burst)
    except Exception:                                  # noqa: BLE001
        burst = None

    bubble = _Bubble(size_hint_x=1, pos_hint={"x": 0, "top": 1})
    name = new_name or tr("The new medic")
    bubble.add_widget(_words(tr("Congratulations!"), "34sp", bold=True))
    bubble.add_widget(_words(tr("You have made a Node Medic."), "21sp", bold=True))
    bubble.add_widget(_words(
        tr("[b]1.[/b]  Unplug the ethernet cable."), "18sp", markup=True))
    bubble.add_widget(_words(
        tr("[b]2.[/b]  Go to {name}. Its screen walks you through plugging in its "
           "Heltec Wireless Tracker — its radio and position finder — then shows "
           "you around.").format(name=name), "18sp", markup=True))
    ok = Button(text=tr("Got it  →"), bold=True, size_hint_y=None, height=dp(56),
                font_size=theme.font_sp("19sp"), background_normal="",
                background_color=theme.hex_to_rgba(INK),
                color=theme.hex_to_rgba(theme.COLORS["accent"]))
    bubble.add_widget(ok)
    root.add_widget(bubble)

    def _fit(*_):
        root.height = bubble.height + dp(_Bubble.TAIL) + dp(8)
        view.height = root.height
    bubble.bind(height=_fit)
    _fit()

    def _close(*_):
        view.dismiss()
        if on_close:
            on_close()
    ok.bind(on_release=_close)

    view.open()
    # fade the bubble in, then the rings
    bubble.opacity = 0
    Clock.schedule_once(lambda dt: Animation(opacity=1, duration=0.25).start(bubble), 0.05)
    if burst is not None:
        Clock.schedule_once(lambda dt: burst.celebrate(), 0.2)
    return view
