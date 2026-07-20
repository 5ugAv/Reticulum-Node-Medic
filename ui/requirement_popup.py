"""A 'you can't do this yet, here's why' hazard card.

When a screen's action needs hardware that isn't attached (or a path that isn't
wired to real hardware yet), we do NOT run an emulated demo and we do NOT dump a
cryptic failed-step log — we raise a clear hazard card that says it straight:
"No board attached — plug one in to continue."

Design: a caution-yellow card with a red outline and a ⚠ glyph — reads as "stop,
read this" at a glance without being an error. Dark text on yellow for contrast.
Shared by BIRTH / PROBE / MITOSIS so every requirement looks and behaves the same.
(Visual language is intentionally simple and themeable — open to Sophie's polish.)
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.widget import Widget

from ui import theme

_YELLOW = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
_RED = theme.hex_to_rgba(theme.COLORS["red"])
_DARK = theme.hex_to_rgba(theme.COLORS["background"])          # text on yellow
_LIGHT = theme.hex_to_rgba(theme.COLORS["text_primary"])       # text on red button
_RADIUS = dp(20)


def requirement_popup(message: str, title: str = "Heads up",
                      under_construction: bool = False) -> ModalView:
    """Show a dismissible caution card stating why a path is unavailable. When
    *under_construction*, the hit is logged for the developer (ui.construction_log)
    so field-hit unbuilt features get caught. Returns the (opened) ModalView."""
    if under_construction:
        from ui.construction_log import log_hit
        log_hit(title, message)
    view = ModalView(size_hint=(0.9, 0.62), background="",
                     background_color=(0, 0, 0, 0.55), auto_dismiss=True)

    card = BoxLayout(orientation="vertical", padding=dp(22), spacing=dp(10))

    def _redraw(*_):
        card.canvas.before.clear()
        with card.canvas.before:
            Color(*_YELLOW)
            RoundedRectangle(pos=card.pos, size=card.size, radius=[_RADIUS] * 4)
            Color(*_RED)
            Line(width=dp(2.5), rounded_rectangle=(
                card.x + dp(1), card.y + dp(1),
                card.width - dp(2), card.height - dp(2), _RADIUS))
    card.bind(pos=_redraw, size=_redraw)

    # A drawn warning triangle with a "!" — the ⚠ emoji renders as tofu in the
    # default font, so we draw it (no font dependency, always crisp).
    icon = FloatLayout(size_hint_y=None, height=dp(66))
    tri = Widget()

    def _tri(*_):
        tri.canvas.after.clear()
        cx, half = tri.center_x, dp(30)
        b, t = tri.y + dp(6), tri.top - dp(4)
        with tri.canvas.after:
            Color(*_RED)
            Line(points=[cx - half, b, cx + half, b, cx, t],
                 width=dp(3), close=True, joint="round", cap="round")
    tri.bind(pos=_tri, size=_tri)
    icon.add_widget(tri)
    bang = Label(text="!", font_size="30sp", bold=True, color=_RED,
                 pos_hint={"center_x": 0.5, "center_y": 0.40})
    icon.add_widget(bang)
    card.add_widget(icon)

    heading = Label(text=title, font_size="23sp", bold=True, size_hint_y=None,
                    height=dp(36), color=_DARK, halign="center", valign="middle")
    heading.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    card.add_widget(heading)

    body = Label(text=message, font_size="16.5sp", color=_DARK,
                 halign="center", valign="top")
    body.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    card.add_widget(body)

    ok = Button(text="Got it", size_hint_y=None, height=dp(54), bold=True,
                font_size="18sp", background_normal="", background_color=_RED,
                color=_LIGHT)
    ok.bind(on_release=lambda *_: view.dismiss())
    card.add_widget(ok)

    view.add_widget(card)
    view.open()
    return view
