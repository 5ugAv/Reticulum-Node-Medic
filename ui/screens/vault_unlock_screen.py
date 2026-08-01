"""The medic's own unlock screen — shown at boot, before HOME.

The Pi's stock console prompt is ugly and frightening; the medic answers for
itself instead. Boot order: splash → THIS screen → home.

The flow (operator spec 2026-08-02):

  1. Passphrase field + "Enter password to open Node Medic".
  2. Three wrong attempts and it stops assuming a typo: the recovery-key field
     appears — the key written down when this medic was first built.
  3. Beneath that, and only there, a SLIDE-to-confirm "Reset this Node Medic"
     with a full red warning. A slide, never a tap, because resetting throws
     away the fleet's records; it must be impossible by accident.

What a reset actually costs, stated plainly on screen: the medic forgets its
records (every node's exact location, certificates, notes). It does NOT harm
the mesh — the nodes keep running and announcing, and can be re-adopted.

The screen is presentation only. Whether a passphrase/key opens the vault is
decided by ``unlock_fn``/``recover_fn`` (injected — the real ones talk to
cryptsetup; tests pass fakes).
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr  # i18n: wrapped — unlock screen prompts/buttons
from provisioning import recovery_key


def _line(text, size="16sp", color="text_primary", bold=False, h=None,
          halign="center"):
    lbl = Label(text=text, font_size=size, bold=bold, halign=halign,
                valign="middle", color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class VaultUnlockScreen(BoxLayout):
    """``on_unlocked()`` fires once the vault is open."""

    def __init__(self, unlock_fn=None, recover_fn=None, reset_fn=None,
                 on_unlocked=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", dp(28))
        kwargs.setdefault("spacing", dp(10))
        super().__init__(**kwargs)
        self._unlock = unlock_fn or (lambda _p: (False, "not wired"))
        self._recover = recover_fn or (lambda _k: (False, "not wired"))
        self._reset = reset_fn or (lambda: (False, "not wired"))
        self._on_unlocked = on_unlocked
        self._failed = 0
        self._render()

    # -- rendering ---------------------------------------------------------

    def _render(self):
        self.clear_widgets()
        self.add_widget(Widget(size_hint_y=None, height=dp(6)))
        self.add_widget(_line(tr("Node Medic"), "30sp", bold=True, h=44))
        self.add_widget(_line(
            tr("Locked — the fleet's records are encrypted on this card."),
            "14.5sp", color="text_secondary", h=26))
        self.add_widget(Widget(size_hint_y=None, height=dp(10)))

        self._field = TextInput(
            hint_text=tr("Password"), multiline=False, password=True,
            size_hint_y=None, height=dp(56), font_size="20sp",
            halign="center")
        try:
            from ui.onscreen_keyboard import bind_field
            bind_field(self._field)
        except Exception:
            pass
        self._field.bind(on_text_validate=lambda *_: self._try_passphrase())
        self.add_widget(self._field)

        go = Button(text=tr("Enter password to open Node Medic"),
                    size_hint_y=None, height=dp(58), bold=True,
                    font_size="17sp", background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        go.bind(on_release=lambda *_: self._try_passphrase())
        self.add_widget(go)

        self._status = _line("", "14sp", color="amber", h=44)
        self.add_widget(self._status)

        # The recovery half only appears once the medic stops assuming a typo.
        self._recovery_box = BoxLayout(orientation="vertical",
                                       size_hint_y=None, spacing=dp(8))
        self._recovery_box.bind(
            minimum_height=self._recovery_box.setter("height"))
        self.add_widget(self._recovery_box)
        self.add_widget(Widget())

    def _show_recovery(self):
        """Three strikes: offer the written-down recovery key, and only then
        the (slide-guarded) reset."""
        box = self._recovery_box
        if box.children:
            return                                  # already showing
        box.add_widget(_line(
            tr("Forgotten your password? Enter the recovery key you wrote "
               "down when this Node Medic was first built."),
            "14sp", color="accent", h=44))
        self._key_field = TextInput(
            hint_text=tr("XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX"),
            multiline=False, size_hint_y=None, height=dp(52),
            font_size="17sp", halign="center")
        try:
            from ui.onscreen_keyboard import bind_field
            bind_field(self._key_field)
        except Exception:
            pass
        self._key_field.bind(on_text_validate=lambda *_: self._try_recovery())
        box.add_widget(self._key_field)
        use = Button(text=tr("Unlock with recovery key"), size_hint_y=None,
                     height=dp(52), bold=True, font_size="16sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        use.bind(on_release=lambda *_: self._try_recovery())
        box.add_widget(use)
        box.add_widget(Widget(size_hint_y=None, height=dp(14)))
        box.add_widget(self._reset_block())

    def _reset_block(self):
        """The last resort — walled off behind a slide and a red warning."""
        wrap = BoxLayout(orientation="vertical", size_hint_y=None,
                         padding=dp(10), spacing=dp(6))
        wrap.bind(minimum_height=wrap.setter("height"))
        from kivy.graphics import Color, Line, RoundedRectangle
        with wrap.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["red"], 0.10))
            rect = RoundedRectangle(radius=[dp(8)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["red"]))
            border = Line(width=dp(1.5))

        def _sync(*_):
            rect.pos, rect.size = wrap.pos, wrap.size
            border.rounded_rectangle = (wrap.x, wrap.y, wrap.width,
                                        wrap.height, dp(8))
        wrap.bind(pos=_sync, size=_sync)

        wrap.add_widget(_line(tr("Lost the password AND the recovery key?"),
                              "15sp", bold=True, color="red", h=26))
        wrap.add_widget(_line(
            tr("Resetting erases this medic's records for good: every node's "
               "saved location, its birth certificates and your notes. It "
               "does NOT harm your mesh — the nodes keep running, and you can "
               "adopt them again. There is no undo."),
            "13sp", color="text_secondary", h=76))
        from ui.widgets.slide_to_power import SlideToPowerOff
        wrap.add_widget(SlideToPowerOff(
            on_power_off=self._do_reset,
            hint_text=tr("slide to erase and start over  →")))
        return wrap

    # -- actions -----------------------------------------------------------

    def _try_passphrase(self):
        pw = (self._field.text or "").strip()
        if not pw:
            self._status.text = tr("Enter your password.")
            return
        ok, msg = self._unlock(pw)
        if ok:
            self._status.color = theme.hex_to_rgba(theme.COLORS["green"])
            self._status.text = tr("Unlocked.")
            if self._on_unlocked:
                self._on_unlocked()
            return
        self._failed += 1
        self._field.text = ""
        left = recovery_key.ATTEMPTS_BEFORE_RECOVERY - self._failed
        if recovery_key.should_offer_recovery(self._failed):
            self._status.text = msg or tr("That password didn't open it.")
            self._show_recovery()
        else:
            self._status.text = (
                tr("That password didn't open it — {n} attempts left.")
                .format(n=max(0, left)))

    def _try_recovery(self):
        raw = (self._key_field.text or "").strip()
        if not recovery_key.is_wellformed(raw):
            self._status.text = tr(
                "That doesn't look like a recovery key (32 characters, in "
                "eight groups of four).")
            return
        ok, msg = self._recover(recovery_key.normalize(raw))
        if ok:
            self._status.color = theme.hex_to_rgba(theme.COLORS["green"])
            self._status.text = tr("Unlocked with the recovery key. Set a new "
                                   "password in Settings.")
            if self._on_unlocked:
                self._on_unlocked()
        else:
            self._status.text = msg or tr("That recovery key didn't open it.")

    def _do_reset(self):
        ok, msg = self._reset()
        self._status.color = theme.hex_to_rgba(
            theme.COLORS["green" if ok else "red"])
        self._status.text = msg or (tr("Reset — starting fresh.") if ok
                                    else tr("Couldn't reset."))
        if ok and self._on_unlocked:
            self._on_unlocked()
