"""Recommended hardware — which node to build, and what it really costs.

Operator, 2026-08-31: "this is probably a page that we need inside Node Medic
as recommended hardware for builds." Someone standing at the BIRTH chooser has
already asked the question this page answers — what kind of node do I need,
and what should I buy to make it? Until now the answer lived only in the
builders' heads and a markdown file nobody in the field can read.

Three jobs, three builds, and one trap worth naming loudly: bolting a
Raspberry Pi to an expensive low-power radio to "save battery" is both the
dearest build and the shortest-lived, because a Pi draws 10-20x what the radio
does. The full reasoning and the solar sizing live in
docs/WHICH_NODE_TO_BUILD.md and docs/CHEAPEST_NODE.md; this screen is the
field-readable version.

Deliberately NOT a table: tables are for reading and comparing, and this is a
page someone acts from ([[lists-as-tick-boxes]]) — and a table pasted into a
message collapses into an unreadable list, which is how the operator found the
problem.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.i18n import tr  # i18n: wrapped — hardware advice page


def _line(text, size="15sp", color="text_primary", bold=False):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                color=theme.hex_to_rgba(theme.COLORS[color]),
                halign="left", valign="top", markup=True, size_hint_y=None)
    lbl.bind(width=lambda w, v: setattr(w, "text_size", (v, None)),
             texture_size=lambda w, s: setattr(w, "height", s[1] + dp(4)))
    return lbl


class HardwareAdviceScreen(BoxLayout):
    """Static reference: what to build for which job, and the real costs."""

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", padding=dp(16),
                         spacing=dp(8), **kwargs)
        self.add_widget(_line(tr("What should I build?"), "22sp",
                              bold=True))
        scroll = ScrollView()
        body = BoxLayout(orientation="vertical", spacing=dp(10),
                         size_hint_y=None, padding=[0, dp(4), 0, dp(20)])
        body.bind(minimum_height=lambda w, v: setattr(w, "height", v))

        for head, cost, lines in self._sections():
            body.add_widget(_line(head, "18sp", "accent", bold=True))
            body.add_widget(_line(cost, "14sp", "green", bold=True))
            for ln in lines:
                body.add_widget(_line("  •  " + ln, "14sp",
                                      "text_secondary"))

        body.add_widget(_line(tr("Whatever you build"), "18sp", "accent",
                              bold=True))
        for ln in (
            tr("Right band: 915 MHz for Australia, NZ and the US; 868 for "
               "Europe. A 433 MHz module looks identical and will never join "
               "your network."),
            tr("Fit an antenna BEFORE powering it — transmitting with none "
               "damages the radio."),
            tr("3.3 V to the radio. Never 5 V."),
            tr("One Node Medic can flash, birth and repair every node here."),
        ):
            body.add_widget(_line("  •  " + ln, "14sp", "amber"))

        scroll.add_widget(body)
        self.add_widget(scroll)

    def _sections(self):
        return [
            (tr("1. The everyday node - as many as you can make"),
             tr("About AU$7 each (AU$5 in tens)"),
             [tr("Classic ESP32 devkit + RFM95/SX1276 radio module."),
              tr("This is what most of a network should be made of - it "
                 "routes and relays as well as a $60 board."),
              tr("What you give up is battery life, a screen, a case and "
                 "GPS - not networking."),
              tr("The antenna is FREE: 8.2 cm of wire is a quarter-wave at "
                 "915 MHz. On our own bench an 8 cm stub beat a 40 cm whip "
                 "and a $40 branded antenna."),
              tr("Power: mains, a USB power bank, or solar with a real "
                 "panel - the ESP32 is not a low-power part.")]),
            (tr("2. The remote node - put it somewhere and leave it"),
             tr("AU$30-60"),
             [tr("An nRF52840 + SX1262 board ON ITS OWN, as a transport "
                 "node: RAK4631, Heltec T114, Heltec Mesh Solar, Seeed "
                 "SenseCAP Solar Node."),
              tr("The radio board IS the node - it needs no computer "
                 "attached."),
              tr("It sleeps at microamps and wakes for packets; listening "
                 "costs about 5-10 mA. Weeks on one 18650, indefinitely "
                 "with a 2 W panel."),
              tr("DO NOT bolt a Raspberry Pi to one of these to save "
                 "battery. A Pi draws 10-20x what the radio does - that "
                 "pairing is the dearest build AND the shortest-lived."),
              tr("The purpose-built solar ones arrive with panel, battery "
                 "and weatherproofing already solved.")]),
            (tr("3. The message-holder - for people who are offline"),
             tr("AU$60+ on mains; AU$120-200 done properly on solar"),
             [tr("A Raspberry Pi + any supported radio."),
              tr("ONLY this kind of node can hold messages for someone "
                 "whose device is switched off. A transport node cannot."),
              tr("A network wants a few of these, not many - one in a hall, "
                 "a shop, a home with power."),
              tr("ON SOLAR a Pi needs REAL hardware: a Pi Zero 2 W (about "
                 "15 Wh a day), a 20 W panel minimum, about 45 Wh of "
                 "battery for three cloudy days, and a proper charge "
                 "controller."),
              tr("A 5 W panel matches a perfect day and dies on the first "
                 "cloudy one. Undervoltage corrupts SD cards quietly."),
              tr("If mains power is anywhere nearby, use it - the money is "
                 "better spent on more everyday nodes.")]),
        ]
