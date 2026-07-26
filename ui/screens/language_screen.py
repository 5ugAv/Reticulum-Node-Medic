"""Language picker — Settings ▸ Language.

A simple scrollable list of the languages Node Medic can render (from
``ui.i18n.available_languages()``): each row shows the native name plus its
English name, and tapping one persists the choice via ``ui.i18n.set_language``.

Language applies on the NEXT app start (every screen is built once, at launch,
in the chosen language — live re-render would mean rebuilding the whole UI), so
selecting one shows a toast saying it takes effect after a restart. The manager
restarts the app on deploy.

The list of languages + the font/script gating all live in ``ui.i18n`` — this
module is a thin view. It only lists LATIN-SCRIPT languages today because the
bundled font can't render other scripts (see ``ui.i18n`` for the caveat).

# i18n: wrapped
"""

from __future__ import annotations

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.i18n import available_languages, current_language, set_language, tr


def _line(text, bold=False, size="15sp", color="text_primary", h=30):
    lbl = Label(text=text, bold=bold, font_size=size, halign="left", valign="middle",
                size_hint_y=None, height=dp(h),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class LanguageScreen(BoxLayout):
    """Pick the language Node Medic runs in. ``on_selected(code)`` (optional) is
    called after a choice is persisted, so the host app can react if it wants."""

    def __init__(self, on_selected=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(10)
        self.padding = dp(16)
        self._on_selected = on_selected

        self.add_widget(_line(tr("Language"), bold=True, size="24sp", h=44))
        self.add_widget(_line(tr("Choose the language Node Medic runs in."),
                              size="13.5sp", color="text_secondary", h=28))

        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False, bar_width=dp(4))
        body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        body.bind(minimum_height=body.setter("height"))
        current = current_language()
        self._rows = {}
        for code, native, english in available_languages():
            # native name is the headline; the English name orients an operator
            # who doesn't yet read the native one. English rows just say "English".
            label = native if native == english else f"{native}  ·  {english}"
            row = Button(text=label, size_hint_y=None, height=dp(60), halign="left",
                         valign="middle", font_size="18sp", bold=True,
                         background_normal="", background_down="")
            row.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
            row.bind(on_release=lambda _b, c=code: self._select(c))
            self._rows[code] = row
            body.add_widget(row)
        scroll.add_widget(body)
        self.add_widget(scroll)

        self.add_widget(_line(
            tr("More languages will follow. The current display font renders Latin "
               "scripts only (Spanish, French, German, Portuguese, Italian, "
               "Indonesian)."),
            size="12sp", color="text_secondary", h=52))

        self._paint(current)

    def _paint(self, current):
        for code, row in self._rows.items():
            on = code == current
            row.background_color = theme.hex_to_rgba(
                theme.COLORS["green" if on else "surface"])
            row.color = theme.hex_to_rgba(
                theme.COLORS["background" if on else "text_primary"])

    def _select(self, code):
        saved = set_language(code)
        self._paint(saved)
        if self._on_selected:
            self._on_selected(saved)
        self._toast(tr("Language set — it applies when Node Medic restarts."))

    def _toast(self, message):
        """Brief, auto-dismissing confirmation (mirrors the app's mode toast)."""
        lbl = Label(text=message, halign="center", valign="middle",
                    padding=(dp(16), dp(16)))
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        p = Popup(title=tr("Language"), content=lbl, size_hint=(0.82, 0.3),
                  auto_dismiss=True)
        p.open()
        Clock.schedule_once(lambda dt: p.dismiss(), 3.5)
