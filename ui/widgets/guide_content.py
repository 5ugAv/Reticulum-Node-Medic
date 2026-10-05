"""Renders provisioning.network_guide as a scrollable Kivy view.

Shared by the full-screen Settings entry (ui.screens.guide_screen) and the inline
"?" help popup (ui.widgets.help_button) so both always show identical, in-sync
content. Pure presentation — all wording lives in the pure content module.

i18n: wrapped. The English lives in provisioning.network_guide (module-level
data, never translated there — the language is chosen at runtime); every string
is passed through ``tr()`` HERE, at render time, so the catalogs under
assets/i18n key on the exact English sentences of that module.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr
from provisioning import network_guide as g


def _wrap(text, size="15sp", color="text_primary", bold=False):
    """A left-aligned label that wraps to its width and grows to fit its text."""
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="top",
                color=theme.hex_to_rgba(theme.COLORS[color]), size_hint_y=None)
    def _sync(_i, _v):
        lbl.text_size = (lbl.width, None)
        lbl.texture_update()
        lbl.height = lbl.texture_size[1]
    lbl.bind(width=_sync, text=_sync)
    return lbl


def _section_title(text):
    return _wrap(text, size="17sp", color="accent", bold=True)


def _concept(term, body):
    box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2),
                    padding=[0, dp(6), 0, dp(2)])
    box.bind(minimum_height=box.setter("height"))
    box.add_widget(_wrap(term, size="16sp", color="text_primary", bold=True))
    box.add_widget(_wrap(body, size="14.5sp", color="text_secondary"))
    return box


def _role_row(device, role):
    row = BoxLayout(orientation="horizontal", size_hint_y=None, spacing=dp(10),
                    padding=[0, dp(5)])
    row.bind(minimum_height=row.setter("height"))
    dev = _wrap(device, size="14sp", color="text_primary", bold=True)
    dev.size_hint_x = 0.56
    rol = _wrap(role, size="14sp", color="accent")
    rol.size_hint_x = 0.44
    row.add_widget(dev)
    row.add_widget(rol)
    return row


def build_guide_content():
    """A ScrollView with the whole quick-guide, ready to drop into a screen or a
    popup. Grows/scrolls to fit — safe on the medic's short panel."""
    col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6),
                    padding=[dp(4), dp(4)])
    col.bind(minimum_height=col.setter("height"))

    # Every string is translated at this point — the data module keeps the
    # English keys; tr() looks each one up in the catalog of the language the
    # operator chose (and hands the English back when a catalog lacks it).
    for term, body in g.CONCEPTS:
        col.add_widget(_concept(tr(term), tr(body)))

    col.add_widget(_divider())
    col.add_widget(_section_title(tr(g.GOLDEN_RULE_TITLE)))
    for para in g.GOLDEN_RULE_BODY:
        col.add_widget(_wrap(tr(para), size="14.5sp", color="text_secondary"))

    col.add_widget(_divider())
    col.add_widget(_section_title(tr("Which device does which job?")))
    for device, role in g.ROLES:
        col.add_widget(_role_row(tr(device), tr(role)))

    col.add_widget(_divider())
    col.add_widget(_section_title(tr(g.REACH_TITLE)))
    for para in g.REACH_BODY:
        col.add_widget(_wrap(tr(para), size="14.5sp", color="text_secondary"))

    col.add_widget(_divider())
    col.add_widget(_section_title(tr(g.BUILD_TITLE)))
    for head, cost, lines in g.BUILD_SECTIONS:
        col.add_widget(_wrap(tr(head), size="16sp", color="accent", bold=True))
        col.add_widget(_wrap(tr(cost), size="14sp", color="green", bold=True))
        for ln in lines:
            col.add_widget(_wrap("  •  " + tr(ln), size="14sp",
                                 color="text_secondary"))
    for ln in g.BUILD_ALWAYS:
        col.add_widget(_wrap("  •  " + tr(ln), size="14sp", color="amber"))

    col.add_widget(_divider())
    col.add_widget(_section_title(tr(g.RADIO_TITLE)))
    # The labels ("Frequency", "Spreading factor (SF)"…) are translated inside
    # radio_lines; the values and units are numbers and SI units, left alone.
    for line in g.radio_lines(translate=tr):
        col.add_widget(_wrap(line, size="15sp", color="text_primary"))

    col.add_widget(Widget(size_hint_y=None, height=dp(8)))
    sv = ScrollView(do_scroll_x=False, bar_width=dp(4))
    sv.add_widget(col)
    return sv


def _divider():
    from kivy.graphics import Color, Rectangle
    w = Widget(size_hint_y=None, height=dp(9))
    with w.canvas:
        Color(*theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.25))
        rect = Rectangle(pos=(w.x, w.center_y), size=(w.width, dp(1)))
    w.bind(pos=lambda *_: setattr(rect, "pos", (w.x, w.center_y + dp(4))),
           size=lambda *_: setattr(rect, "size", (w.width, dp(1))))
    return w
