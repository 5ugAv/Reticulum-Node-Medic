"""A reusable danger-confirm popup: a warning the operator must be able to push
past. Red 'proceed anyway' override on the left, green safe 'cancel' on the right
(matches the birth power-warning convention). Used where an action is normally
blocked but must stay possible in an emergency — e.g. powering off while a flash
is stuck, when leaving Node Medic on could be worse than the brick risk.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup

from ui import theme
from ui.i18n import tr  # i18n: wrapped — danger/power-off confirm dialog copy

#: Shared wording for the power-off-during-flash override (home + settings).
FLASH_POWEROFF_WARNING = tr(
    "WARNING - Node Medic is flashing a board.  Do NOT power off.\n\n"
    "Continuing to power off may brick the connected radio board. Only override "
    "if the flash is genuinely stuck and you have no other choice.")


def confirm_danger(message, title, on_proceed, proceed_text=None,
                   cancel_text=None):
    """Show a modal warning. ``on_proceed`` runs only if the operator taps the red
    override. Tapping outside does nothing (auto_dismiss off) so it can't be
    dismissed by accident. Returns the Popup."""
    if proceed_text is None:
        proceed_text = tr("Proceed anyway")
    if cancel_text is None:
        cancel_text = tr("Cancel")
    body = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(10))
    lbl = Label(text=message, halign="center", valign="middle", font_size="15sp",
                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    body.add_widget(lbl)
    btns = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(58),
                     spacing=dp(10))
    proceed = Button(text=proceed_text, bold=True, font_size="16sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
    cancel = Button(text=cancel_text, bold=True, font_size="16sp",
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
    btns.add_widget(proceed)          # bottom-left = the dangerous override
    btns.add_widget(cancel)           # bottom-right = safe default
    body.add_widget(btns)
    popup = Popup(title=title, content=body, size_hint=(0.92, 0.52),
                  title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                  separator_color=theme.hex_to_rgba(theme.COLORS["red"]),
                  auto_dismiss=False)
    proceed.bind(on_release=lambda *_: (popup.dismiss(), on_proceed()))
    cancel.bind(on_release=lambda *_: popup.dismiss())
    popup.open()
    return popup


#: The card never grows past this fraction of the panel height — the body
#: scrolls past it (ui.requirement_popup's clamp, shared so the two cards
#: cannot drift).
_LEAVE_MAX_H_FRAC = 0.9


def confirm_leave(message, title, on_leave, stay_text, leave_text,
                  leave_color="accent", auto_dismiss=True, on_dismiss=None):
    """'Leave this?' — the caution-yellow card with the red outline that
    ui.requirement_popup shares, so every warning in the tool reads the
    same; two buttons, THE SAFE CHOICE FIRST (the guide's rule, 2026-08-14).
    The birth guide's 'Leave this build?' is this card with
    ``leave_color="red"`` (2026-09-23 — it used to be a second copy of the
    same drawing, and two copies drift).

    Built for the boundary walk's back/home confirm (operator, 2026-09-23:
    "a warning should come up saying confirm exit of boundary walk"). The
    leave button is NOT red by default: red on this tool means Delete /
    Rebirth, and the walk's leave button SAVES the walk (the Stop & save
    rule, 2026-09-21) — a caller whose leaving destroys something passes
    ``leave_color="red"``.

    *auto_dismiss* True (default): tapping outside dismisses = stay, the
    safe answer an accidental touch gives. False is the confirm_danger
    rule — a stray tap does NOTHING and only the two buttons close the
    card; the walk uses it, because a card that a knee can close is a
    card the operator never read. *on_dismiss* runs whenever the card
    closes, by either button or from outside, so a caller holding the
    card as an open-once fuse can let go of it.

    The body scrolls inside a clamp of 0.9 of the window (the
    requirement_popup shape): a Russian sentence on a short panel used to
    push the buttons off the glass. Returns the ModalView."""
    from kivy.core.window import Window
    from kivy.graphics import Color, Line, RoundedRectangle
    from kivy.uix.modalview import ModalView
    from kivy.uix.scrollview import ScrollView
    yellow = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
    red = theme.hex_to_rgba(theme.COLORS["red"])
    green = theme.hex_to_rgba(theme.COLORS["green"])
    dark = theme.hex_to_rgba(theme.COLORS["background"])
    view = ModalView(size_hint=(0.9, None), background="",
                     background_color=(0, 0, 0, 0.55),
                     auto_dismiss=bool(auto_dismiss))
    card = BoxLayout(orientation="vertical", padding=dp(22), spacing=dp(12),
                     size_hint_y=None)
    card.bind(minimum_height=card.setter("height"))
    card.bind(height=lambda _i, h: setattr(view, "height", h))
    radius = dp(20)

    def _redraw(*_):
        card.canvas.before.clear()
        with card.canvas.before:
            Color(*yellow)
            RoundedRectangle(pos=card.pos, size=card.size, radius=[radius] * 4)
            Color(*red)
            Line(width=dp(2.5), rounded_rectangle=(
                card.x + dp(1), card.y + dp(1),
                card.width - dp(2), card.height - dp(2), radius))
    card.bind(pos=_redraw, size=_redraw)
    head = Label(text=title, font_size="23sp", bold=True, size_hint_y=None,
                 height=dp(36), color=dark, halign="center", valign="middle")
    head.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    card.add_widget(head)
    # Everything around the body has a fixed height; what is left under the
    # clamp is the body's, and past that it scrolls (never clips).
    overhead = dp(44) + dp(36) + dp(12) + dp(56) + dp(12)
    scroll = ScrollView(size_hint_y=None, do_scroll_x=False, bar_width=dp(4))
    body = Label(text=message, font_size="16.5sp", color=dark,
                 halign="center", valign="top", size_hint_y=None)
    body.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    body.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1]))
    scroll.add_widget(body)
    card.add_widget(scroll)

    def _fit(*_):
        avail = _LEAVE_MAX_H_FRAC * Window.height - overhead
        floor = min(dp(44), max(avail, 0))
        scroll.height = max(floor, min(body.height, avail))
    body.bind(height=_fit)
    _on_win_resize = lambda *_: _fit()
    Window.bind(height=_on_win_resize)
    _fit()
    row = BoxLayout(orientation="horizontal", size_hint_y=None,
                    height=dp(56), spacing=dp(10))
    stay = Button(text=stay_text, bold=True, font_size="17sp",
                  background_normal="", background_color=green, color=dark)
    stay.bind(on_release=lambda *_: view.dismiss())
    go = Button(text=leave_text, bold=True, font_size="17sp",
                background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS[leave_color]),
                color=(theme.hex_to_rgba(theme.COLORS["text_primary"])
                       if leave_color == "red" else dark))
    go.bind(on_release=lambda *_: (view.dismiss(), on_leave()))
    row.add_widget(stay)                   # the safe choice reads first
    row.add_widget(go)
    card.add_widget(row)
    view.add_widget(card)

    def _closed(*_):
        Window.unbind(height=_on_win_resize)   # no live callback on a dead card
        if callable(on_dismiss):
            on_dismiss()
    view.bind(on_dismiss=_closed)
    view.open()
    return view


def confirm_power_override(message, title, on_proceed):
    """Like confirm_danger, but the override is a GEAR-SHIFT gesture (right, down,
    right) instead of a tappable button — so a mid-flash power-off can never be a
    fat-finger; it takes a deliberate dog-leg drag. 'Keep flashing' stays an easy
    tap to back out."""
    from kivy.uix.anchorlayout import AnchorLayout
    from ui.widgets.gear_shift import GearShiftOverride
    body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
    lbl = Label(text=message, halign="center", valign="middle", font_size="15sp",
                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    body.add_widget(lbl)
    body.add_widget(Label(
        text=tr("To power off anyway: shift the knob  RIGHT, then DOWN, then RIGHT"),
        size_hint_y=None, height=dp(24), bold=True, font_size="12.5sp",
        color=theme.hex_to_rgba(theme.COLORS["red"])))
    holder = AnchorLayout(size_hint_y=None, height=dp(120))
    body.add_widget(holder)
    cancel = Button(text=tr("Keep flashing"), bold=True, font_size="16sp",
                    size_hint_y=None, height=dp(52), background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
    body.add_widget(cancel)
    popup = Popup(title=title, content=body, size_hint=(0.94, 0.62),
                  title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                  separator_color=theme.hex_to_rgba(theme.COLORS["red"]),
                  auto_dismiss=False)
    gear = GearShiftOverride(
        on_complete=lambda: (popup.dismiss(), on_proceed()))
    holder.add_widget(gear)
    cancel.bind(on_release=lambda *_: popup.dismiss())
    popup.open()
    return popup
