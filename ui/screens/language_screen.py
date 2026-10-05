"""Language picker — Settings ▸ Language.

A simple scrollable list of the languages Node Medic can render (from
``ui.i18n.available_languages()``): each row shows the native name plus its
English name, and tapping one persists the choice via ``ui.i18n.set_language``.

Language applies on the NEXT app start (every screen is built once, at launch,
in the chosen language — live re-render would mean rebuilding the whole UI), so
selecting one shows a toast saying it takes effect after a restart. The manager
restarts the app on deploy.

The list of languages + the font/script gating all live in ``ui.i18n`` — this
module is a thin view. It lists what the bundled font can draw (Latin and
Cyrillic today) AND what has a catalog covering the critical path.

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
from ui.i18n import (available_languages, current_language, set_language, tr,
                     coverage, japanese_font_path)
from ui.text_fit import grow_to_text


def _line(text, bold=False, size="15sp", color="text_primary", h=30):
    lbl = Label(text=text, bold=bold, font_size=theme.font_sp(size),
                halign="left", valign="middle",
                size_hint_y=None,
                height=dp(max(h, theme.line_dp(size))),
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
            cov = coverage(code)
            if cov < 0.9:
                # say it on the row, before the tap (readiness ledger #149)
                label += "   " + tr("{pct}% translated — the rest shows in English"
                                    ).format(pct=int(cov * 100))
            row = Button(text=label, size_hint_y=None, height=dp(60), halign="left",
                         valign="middle", font_size="18sp", bold=True,
                         background_normal="", background_down="")
            if code == "ja":
                # The row is drawn in the app's Latin face unless Japanese is
                # already chosen — which would paint 日本語 as three boxes on
                # the one row meant to show it (readiness ledger #150). Noto
                # Sans JP draws the Latin half of the label too.
                ja_font = japanese_font_path()
                if ja_font:
                    row.font_name = ja_font
            row.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
            row.bind(on_release=lambda _b, c=code: self._select(c))
            self._rows[code] = row
            body.add_widget(row)
        scroll.add_widget(body)
        self.add_widget(scroll)

        # TRUE of the list above it, whatever it holds: the old footer named
        # Italian (not offered), omitted four languages that are, and claimed
        # Latin-only under a Russian row (readiness sweep, 2026-10-03).
        # Grows to its text: pinned at 52 dp its four lines ran up under the
        # last row and off the bottom (seen on the glass 2026-10-05).
        self.add_widget(grow_to_text(_line(
            tr("Listed here: every language the display font can draw. A row "
               "that names a percentage is partly translated — the rest shows "
               "in English. More follow as translations land."),
            size="12sp", color="text_secondary", h=52), extra_dp=6))

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
        # there is no restart button anywhere: say how (2026-10-03)
        self._toast(tr("Language set — it applies when Node Medic next starts. "
                       "Slide to power off on the front page, then power back on."))

    def _toast(self, message):
        """Brief, auto-dismissing confirmation (mirrors the app's mode toast)."""
        lbl = Label(text=message, halign="center", valign="middle",
                    padding=(dp(16), dp(16)))
        lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
        # Not a 3.5 s toast: the restart instruction is the one thing the
        # keeper must read, and the only way to restart is to power off and on
        # (readiness ledger #22) — it stays until tapped.
        p = Popup(title=tr("Language"), content=lbl, size_hint=(0.82, 0.3),
                  auto_dismiss=True)
        p.open()
