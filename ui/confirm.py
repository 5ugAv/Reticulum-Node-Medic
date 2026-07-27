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
