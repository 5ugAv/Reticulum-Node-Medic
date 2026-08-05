"""The write-it-down ceremony — shown ONCE, when a Node Medic is first built.

This is the only moment the recovery key ever exists on screen. After it, the
medic holds nothing that can reproduce it: if the operator has not written it
down and later forgets their password, the fleet's records are gone.

So the screen refuses to be clicked through casually. The operator confirms
THREE times, each asking more pointedly (operator spec 2026-08-02) — deliberate
friction, in proportion to a consequence that cannot be undone. The key is
shown large, in groups, with a QR alongside for a phone photo.

Presentation only: the key itself comes from provisioning.recovery_key and is
installed into its own vault keyslot by the caller.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr  # i18n: wrapped — recovery-key ceremony
from provisioning import recovery_key

#: Each confirmation asks harder than the last. The operator taps through all
#: three before the medic will continue.
CONFIRMATIONS = [
    (lambda: tr("Have you written it down?"),
     lambda: tr("This is the only way back in if you forget your password.")),
    (lambda: tr("Really written it down?"),
     lambda: tr("Not a photo you'll delete, not a note on the medic itself — "
                "somewhere you'll still have it in a year.")),
    (lambda: tr("Last check — this key is about to disappear."),
     lambda: tr("Node Medic will never show it again, and cannot recover it. "
                "Without it and your password, this medic's records are lost "
                "for good.")),
]


def _line(text, size="16sp", color="text_primary", bold=False, h=None,
          halign="center"):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold, halign=halign,
                valign="middle", color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(max(h, theme.line_dp(size)))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class RecoveryKeyScreen(BoxLayout):
    """``on_done()`` fires only after all three confirmations."""

    def __init__(self, key: str = None, on_done=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", dp(22))
        kwargs.setdefault("spacing", dp(8))
        super().__init__(**kwargs)
        self.key = key or recovery_key.generate()
        self._on_done = on_done
        self._step = 0
        self._pop = None
        self._render()

    def _render(self):
        self.add_widget(_line(tr("Write this down now"), "26sp", bold=True,
                              h=40, color="warning_yellow"))
        self.add_widget(_line(
            tr("This is your recovery key — the ONLY way into this Node Medic "
               "if you forget your password. It is shown once and never again."),
            "14.5sp", color="text_secondary", h=48))

        # the key itself, big, in two rows of four groups
        gs = recovery_key.groups(self.key)
        for row_groups in (gs[:4], gs[4:]):
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(54), spacing=dp(8))
            for g in row_groups:
                row.add_widget(_line(g, "28sp", bold=True, color="accent"))
            self.add_widget(row)

        self.add_widget(_line(
            tr("Keep it away from the medic — a key taped to the case "
               "protects nothing."), "13sp", color="text_secondary", h=24))

        qr = self._qr_widget()
        if qr is not None:
            holder = BoxLayout(orientation="vertical", size_hint_y=None,
                               height=dp(140))
            holder.add_widget(qr)
            self.add_widget(holder)

        self.add_widget(Widget())
        go = Button(text=tr("I've written it down"), size_hint_y=None,
                    height=dp(58), bold=True, font_size="18sp",
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        go.bind(on_release=lambda *_: self._ask(0))
        self.add_widget(go)

    def _qr_widget(self):
        try:
            from ui.qr import qr_matrix
            from ui.screens.birth_screen import QRCodeWidget
            m = qr_matrix(self.key)
            if not m:
                return None
            return QRCodeWidget(m, scale=dp(2.6))
        except Exception:
            return None

    # -- the three confirmations -------------------------------------------

    def _ask(self, step: int):
        if getattr(self, "_pop", None) is not None:      # doubled-tap guard
            return
        if step >= len(CONFIRMATIONS):
            self._finish()
            return
        title_fn, body_fn = CONFIRMATIONS[step]
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(14))
        box.add_widget(_line(body_fn(), "16sp", h=110))
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(54), spacing=dp(10))
        popup = Popup(title=title_fn(), content=box, size_hint=(0.9, 0.55),
                      auto_dismiss=False,
                      title_color=theme.hex_to_rgba(
                          theme.COLORS["warning_yellow"]))
        self._pop = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_pop", None))

        back = Button(text=tr("Not yet — show me the key"), bold=True,
                      background_normal="",
                      background_color=theme.hex_to_rgba(
                          theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        back.bind(on_release=lambda *_: popup.dismiss())
        yes = Button(text=tr("Yes"), bold=True, background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _next(*_):
            popup.dismiss()
            self._step = step + 1
            from kivy.clock import Clock
            Clock.schedule_once(lambda _dt: self._ask(step + 1), 0.15)
        yes.bind(on_release=_next)
        row.add_widget(back)
        row.add_widget(yes)
        box.add_widget(row)
        popup.open()

    def _finish(self):
        if self._on_done:
            self._on_done(self.key)
