"""A 'you can't do this yet, here's why' hazard card.

When a screen's action needs hardware that isn't attached (or a path that isn't
wired to real hardware yet), we do NOT run an emulated demo and we do NOT dump a
cryptic failed-step log — we raise a clear hazard card that says it straight:
"No board attached — plug one in to continue."

Design: a caution-yellow card with a red outline and a ⚠ glyph — reads as "stop,
read this" at a glance without being an error. Dark text on yellow for contrast.
Shared by BIRTH / PROBE / MITOSIS so every requirement looks and behaves the same.
(Visual language is intentionally simple and themeable — open to a design pass.)

WHY THE BODY SCROLLS (UX review, 2026-08-14 "the failure card can't show its
failure"): this same card is the shared failure surface for BIRTH / PROBE / the
guided flow, and those callers hand it 400-500 character, 9-11 line messages —
the [FAIL] line plus per-board button-press recovery. The old fixed card gave
the body ~58dp (2-3 lines) and a plain Label simply CLIPPED the rest, so the very
instruction the operator needed at the moment of confusion was off-screen. Now
the body lives in a ScrollView and the card grows with its content up to a clamp
that keeps it inside the panel — the full message is always reachable, and
nothing overflows the glass.

And WHY IT CAN SHOW A BOARD (show-don't-tell doctrine): a failure that tells the
operator "hold BOOT, tap RST" must show the board those buttons are on — the
board key is known at the failure moment, so callers can pass image_path and the
photo rides above the text.
"""

from __future__ import annotations

from kivy.core.window import Window
from kivy.metrics import dp
from kivy.graphics import Color, Line, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr  # i18n: wrapped — the card's own default title/button

_YELLOW = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
_GREEN = theme.hex_to_rgba(theme.COLORS["green"])
_RED = theme.hex_to_rgba(theme.COLORS["red"])
_DARK = theme.hex_to_rgba(theme.COLORS["background"])          # text on yellow
_LIGHT = theme.hex_to_rgba(theme.COLORS["text_primary"])       # text on red button
_RADIUS = dp(20)

#: The card never grows past this fraction of the panel height — the body
#: scrolls once its text would push the card past the clamp, so a 9-11 line
#: failure message stays fully readable without the card ever leaving the glass.
_MAX_H_FRAC = 0.9


def requirement_popup(message: str, title: str = "Heads up",
                      under_construction: bool = False,
                      tone: str = "warning",
                      image_path: str | None = None) -> ModalView:
    """Show a dismissible card stating why a path is unavailable (or that it
    FINISHED). *tone*: "warning" = caution-yellow card; "success" = GREEN card
    (still the bold red outline) — a positive confirmation reads as a win, not
    a warning (operator spec 2026-07-31). When *under_construction*, the hit is
    logged for the developer (ui.construction_log).

    *image_path*: an optional photo (e.g. ui.board_images.image_for(key)) shown
    above the text — so a failure that says "press this button" also shows the
    board it means. None (the default) = no image, exactly as before.

    The body scrolls, so *message* may be any length: it is NEVER truncated.
    Returns the ModalView."""
    if under_construction:
        from ui.construction_log import log_hit
        log_hit(title, message)
    # Content-sized card (size_hint_y=None + minimum_height), clamped so it can
    # not exceed the panel — the ScrollView absorbs anything taller.
    view = ModalView(size_hint=(0.9, None), background="",
                     background_color=(0, 0, 0, 0.55), auto_dismiss=True)

    card = BoxLayout(orientation="vertical", padding=dp(22), spacing=dp(10),
                     size_hint_y=None)
    card.bind(minimum_height=card.setter("height"))
    card.bind(height=lambda _i, h: setattr(view, "height", h))

    def _redraw(*_):
        card.canvas.before.clear()
        with card.canvas.before:
            Color(*(_GREEN if tone == "success" else _YELLOW))
            RoundedRectangle(pos=card.pos, size=card.size, radius=[_RADIUS] * 4)
            Color(*_RED)
            Line(width=dp(2.5), rounded_rectangle=(
                card.x + dp(1), card.y + dp(1),
                card.width - dp(2), card.height - dp(2), _RADIUS))
    card.bind(pos=_redraw, size=_redraw)

    # Running tally of every FIXED-height piece around the body, so the body's
    # scroll height can be clamped to whatever room is left under _MAX_H_FRAC.
    overhead = dp(44)                        # padding top + bottom

    # Success cards carry NO warning glyph (operator 2026-07-31: a triangle on
    # a green 'finished' card reads as a warning artefact).
    if tone != "success":
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
        overhead += dp(66) + dp(10)          # icon + one spacing gap

    heading = Label(text=tr(title), font_size="23sp", bold=True, size_hint_y=None,
                    height=dp(36), color=_DARK, halign="center", valign="middle")
    heading.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    card.add_widget(heading)
    overhead += dp(36) + dp(10)              # heading + its spacing gap

    # Show-don't-tell: the board the message is telling the operator to press
    # buttons on. Only added when a real path is passed AND resolves — a caller
    # that has no board (e.g. "no board attached") passes None and gets the old
    # text-only card. A missing file just shows an empty box, harmless.
    if image_path:
        img = Image(source=image_path, size_hint_y=None, height=dp(120),
                    allow_stretch=True, keep_ratio=True)
        card.add_widget(img)
        overhead += dp(120) + dp(10)         # photo + its spacing gap

    # Success cards are read at arm's length from a bench, and they have the
    # room the warning triangle would have used — so the instructions inside
    # get the bigger type (operator, 2026-08-14: "the text inside the green
    # box... there's plenty of space here"). Both sizes go through the theme's
    # type scale so a global bump moves them together.
    scroll = ScrollView(size_hint_y=None, do_scroll_x=False, bar_width=dp(4))
    body = Label(text=message, size_hint_y=None,
                 font_size=theme.font_sp("21sp" if tone == "success" else "16.5sp"),
                 color=_DARK, halign="center", valign="top")
    body.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    body.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1]))
    scroll.add_widget(body)
    card.add_widget(scroll)
    overhead += dp(54) + dp(10)              # OK button + its spacing gap below

    def _fit(*_):
        # Room left for the body under the clamp. Normally we floor the body at
        # one comfortable line so a short window still shows something to scroll
        # — but the floor must never win over the 0.9H clamp, or the card would
        # exceed the glass (the exact clip this change kills). So the floor is
        # itself capped at the room available: on any panel shorter than
        # ~450dp, where one line no longer fits under 0.9H, the card stays
        # inside the screen and the body just gets tighter.
        avail = _MAX_H_FRAC * Window.height - overhead
        floor = min(dp(44), max(avail, 0))
        scroll.height = max(floor, min(body.height, avail))
    body.bind(height=_fit)
    # Re-fit if the window resizes — but hold the callback so it can be dropped
    # when this card is dismissed. Without the unbind, every popup over a long
    # kiosk session would leave a live Window callback closed over its dead
    # widgets (an unbounded leak the fixed-size card never had).
    _on_win_resize = lambda *_: _fit()
    Window.bind(height=_on_win_resize)
    view.bind(on_dismiss=lambda *_: Window.unbind(height=_on_win_resize))
    _fit()

    ok = Button(text=tr("Got it"), size_hint_y=None, height=dp(54), bold=True,
                font_size="18sp", background_normal="", background_color=_RED,
                color=_LIGHT)
    ok.bind(on_release=lambda *_: view.dismiss())
    card.add_widget(ok)

    view.add_widget(card)
    view.open()
    return view
