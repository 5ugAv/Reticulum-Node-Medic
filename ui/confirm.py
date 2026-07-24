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

#: Shared wording for the power-off-during-flash override (home + settings).
FLASH_POWEROFF_WARNING = (
    "WARNING - Node Medic is flashing a board.  Do NOT power off.\n\n"
    "Continuing to power off may brick the connected radio board. Only override "
    "if the flash is genuinely stuck and you have no other choice.")


def confirm_danger(message, title, on_proceed, proceed_text="Proceed anyway",
                   cancel_text="Cancel"):
    """Show a modal warning. ``on_proceed`` runs only if the operator taps the red
    override. Tapping outside does nothing (auto_dismiss off) so it can't be
    dismissed by accident. Returns the Popup."""
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
