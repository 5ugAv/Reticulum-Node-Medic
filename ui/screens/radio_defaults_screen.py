"""Settings ▸ Default radio parameters (item 1).

Shows the tool-wide radio defaults that BIRTH pre-fills (frequency, bandwidth,
spreading factor, coding rate, TX power). Editable, but with a prominent
leave-them-alone warning. A "Suggested settings by region" picker fills all five
for regions that don't use 915 MHz — and confirms, because a different band means
a SEPARATE regional mesh.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput

from ui import theme
from ui.onscreen_keyboard import bind_field
from provisioning import radio_defaults as rd

_FIELDS = [
    ("freq", "Frequency (MHz)"), ("bw", "Bandwidth (kHz)"),
    ("sf", "Spreading factor"), ("cr", "Coding rate"), ("txp", "TX power (dBm)"),
]


def _line(text, size="15sp", color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=size, bold=bold, halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class RadioDefaultsScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(14)
        self.spacing = dp(8)
        self.add_widget(_line("Default radio parameters", bold=True, size="22sp", h=40))

        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        col.bind(minimum_height=col.setter("height"))

        # prominent warning
        warn = BoxLayout(orientation="vertical", size_hint_y=None, padding=dp(10))
        warn.bind(minimum_height=warn.setter("height"))
        with warn.canvas.before:
            from kivy.graphics import Color, RoundedRectangle
            self._wc = Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"], 0.16))
            self._wr = RoundedRectangle(radius=[dp(8)] * 4)
        warn.bind(pos=lambda *_: setattr(self._wr, "pos", warn.pos),
                  size=lambda *_: setattr(self._wr, "size", warn.size))
        warn.add_widget(_line(
            "These are the tool-wide defaults every BIRTH pre-fills. Leave them "
            "alone unless you know exactly why — mismatched parameters keep a node "
            "off the mesh, and a different frequency band builds a SEPARATE mesh.",
            size="13.5sp", color="warning_yellow", h=78))
        col.add_widget(warn)

        # regional presets
        col.add_widget(_line("Suggested settings by region", bold=True, size="15sp",
                             color="accent", h=26))
        for key in rd.preset_keys():
            b = Button(text=rd.preset_label(key), size_hint_y=None, height=dp(46),
                       halign="left", font_size="14.5sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            b.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(20), v[1])))
            b.bind(on_release=lambda _b, k=key: self._confirm_preset(k))
            col.add_widget(b)

        # editable fields
        col.add_widget(_line("Current defaults", bold=True, size="15sp",
                             color="accent", h=26))
        cur = rd.load_defaults()
        self._inputs = {}
        for key, label in _FIELDS:
            row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(50),
                            spacing=dp(8))
            row.add_widget(_line(label, size="15sp"))
            v = cur[key]
            ti = TextInput(text=f"{v:g}" if key in ("freq", "bw") else str(v),
                           multiline=False, size_hint=(None, None), width=dp(150),
                           height=dp(44), font_size="18sp",
                           input_filter="float" if key in ("freq", "bw") else "int")
            bind_field(ti, numeric=True)
            self._inputs[key] = ti
            row.add_widget(ti)
            col.add_widget(row)

        save = Button(text="Save defaults", size_hint_y=None, height=dp(54),
                      bold=True, font_size="18sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        save.bind(on_release=lambda *_: self._save())
        col.add_widget(save)
        self._status = _line("", size="13sp", color="green", h=24)
        col.add_widget(self._status)

        body.add_widget(col)
        self.add_widget(body)

    def _read_fields(self):
        vals = {}
        for key, _ in _FIELDS:
            vals[key] = self._inputs[key].text.strip()
        return vals

    def _fill_fields(self, params):
        for key, _ in _FIELDS:
            v = params[key]
            self._inputs[key].text = f"{v:g}" if key in ("freq", "bw") else str(v)

    def _save(self):
        stored = rd.save_defaults(self._read_fields())
        self._fill_fields(stored)                    # reflect coercion
        self._status.text = f"Saved — BIRTH pre-fills {rd.summary(stored)}"

    def _confirm_preset(self, key):
        params = rd.preset_params(key)
        if params is None:
            return
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", text=(
            f"[b]{rd.preset_label(key)}[/b]\n\n{rd.summary(params)}\n\n"
            f"{rd.preset_note(key)}\n\n"
            "Nodes built with these settings form a SEPARATE regional mesh from "
            "nodes on a different band. Apply as the tool defaults?"), markup=True)
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title="Apply regional preset", content=box, size_hint=(0.9, 0.6))
        cancel = Button(text="Cancel", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        apply_b = Button(text="Apply", bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _apply(*_):
            popup.dismiss()
            stored = rd.save_defaults(params)
            self._fill_fields(stored)
            self._status.text = (f"Applied {rd.preset_label(key)} — "
                                 f"BIRTH pre-fills {rd.summary(stored)}")
        apply_b.bind(on_release=_apply)
        row.add_widget(cancel)
        row.add_widget(apply_b)
        box.add_widget(row)
        popup.open()
