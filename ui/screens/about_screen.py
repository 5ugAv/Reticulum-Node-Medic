"""Settings ▸ About (item 9, read-only).

Shows this software's provenance: the git build it's running, an honest
test-suite indicator (collected count — NOT a live run, NOT a fabricated
"passing"), how long the unit has been up, and the MIT licence + repo link —
then the thanks and the support section the front page's cross opens
(ui.screens.credits_screen.CreditsBody), so the two doors show one page.

The git / pytest-collect reads are quick subprocesses, so they're fetched on a
thread and posted back to the UI via Clock — the screen never blocks.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.i18n import tr  # i18n: wrapped — every literal this screen draws
from provisioning import about


def _line(text, size="15sp", color="text_primary", bold=False, h=None, mono=False):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]),
                font_name="RobotoMono-Regular" if mono else "Roboto")
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(max(h, theme.line_dp(size, mono=mono)))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


def _field(title, value, mono=False, value_color="text_primary", size="16sp"):
    box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2),
                    padding=[0, dp(4)])
    box.bind(minimum_height=box.setter("height"))
    box.add_widget(_line(title, size="12.5sp", color="accent", bold=True, h=20))
    v = _line(value, size=size, color=value_color, mono=mono)
    v.size_hint_y = None
    v.bind(texture_size=lambda i, ts: setattr(i, "height", max(dp(24), ts[1])))
    box.add_widget(v)
    return box, v


class AboutScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(16)
        self.spacing = dp(10)
        self.add_widget(_line(tr("About"), bold=True, size="22sp", h=40))

        scroll = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        col.bind(minimum_height=col.setter("height"))

        self._version_box, self._version_v = _field(tr("Software version"),
                                                     tr("reading…"), mono=True)
        col.add_widget(self._version_box)
        self._tests_box, self._tests_v = _field(tr("Test suite"), "…")
        col.add_widget(self._tests_box)
        self._uptime_box, self._uptime_v = _field(tr("Uptime"), "…")
        col.add_widget(self._uptime_box)
        self._license_box, self._license_v = _field(tr("Licence"), about.LICENSE)
        col.add_widget(self._license_box)
        # 13sp: at 16sp the URL broke mid-word at the right edge (2026-10-06)
        self._repo_box, self._repo_v = _field(tr("Repository"), "…", mono=True, size="13sp")
        col.add_widget(self._repo_box)

        # The same thanks + support page the front page's cross opens
        # (operator, 2026-10-04): one page, two doors — the Easter egg keeps
        # its own screen, this one shows the body under the build facts.
        from ui.screens.credits_screen import CreditsBody
        col.add_widget(_line(tr("With thanks"), bold=True, size="22sp",
                             color="red", h=40))
        col.add_widget(CreditsBody())

        scroll.add_widget(col)
        self.add_widget(scroll)
        self._load()

    def _load(self):
        def work():
            s = about.summary()
            Clock.schedule_once(lambda dt: self._show(s), 0)
        threading.Thread(target=work, daemon=True).start()

    def _show(self, s):
        self._version_v.text = s["version"]
        self._tests_v.text = s["test_status"]
        self._uptime_v.text = s["uptime"]
        self._license_v.text = s["license"]
        self._repo_v.text = s["repo"] or "(no origin remote configured)"
