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
from ui.text_fit import grow_to_text
from ui.i18n import tr  # i18n: wrapped — radio-defaults labels/warnings/popups
from ui.onscreen_keyboard import bind_field
from provisioning import radio_defaults as rd

_FIELDS = [
    ("freq", "Frequency (MHz)"), ("bw", "Bandwidth (kHz)"),
    ("sf", "Spreading factor"), ("cr", "Coding rate"), ("txp", "TX power (dBm)"),
]


def _line(text, size="15sp", color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(max(h, theme.line_dp(size)))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class RadioDefaultsScreen(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(14)
        self.spacing = dp(8)
        from ui.widgets.help_button import HelpButton
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                         spacing=dp(8))
        head.add_widget(_line(tr("Default radio parameters"), bold=True, size="22sp"))
        head.add_widget(HelpButton())
        self.add_widget(head)

        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        col.bind(minimum_height=col.setter("height"))

        # prominent warning
        warn = BoxLayout(orientation="vertical", size_hint_y=None, padding=dp(10))
        warn.bind(minimum_height=warn.setter("height"))
        with warn.canvas.before:
            from kivy.graphics import Color, Line, RoundedRectangle
            self._wc = Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"], 0.16))
            self._wr = RoundedRectangle(radius=[dp(8)] * 4)
            # a red ring, and bigger bold text below: the warning has to
            # stand out from the settings under it (operator, 2026-10-04)
            Color(*theme.hex_to_rgba(theme.COLORS["red"]))
            self._wo = Line(width=dp(2.2))

        def _sync_warn(*_):
            self._wr.pos, self._wr.size = warn.pos, warn.size
            self._wo.rounded_rectangle = (warn.x, warn.y, warn.width,
                                          warn.height, dp(8))
        warn.bind(pos=_sync_warn, size=_sync_warn)
        warn.add_widget(grow_to_text(_line(tr(
            "These are the tool-wide defaults every BUILD pre-fills. Leave them "
            "alone unless you know exactly why — mismatched parameters keep a node "
            "off the mesh, and a different frequency band builds a SEPARATE mesh."),
            size="15.5sp", color="warning_yellow", bold=True)))
        col.add_widget(warn)

        # regional presets
        col.add_widget(_line(tr("Suggested settings by region"), bold=True, size="15sp",
                             color="accent", h=26))
        for key in rd.preset_keys():
            b = Button(text=rd.preset_label(key), size_hint_y=None, height=dp(46),
                       halign="left", valign="middle", font_size="14.5sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            b.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(20), v[1])))
            b.bind(on_release=lambda _b, k=key: self._confirm_preset(k))
            col.add_widget(b)

        # editable fields
        col.add_widget(_line(tr("Current defaults"), bold=True, size="15sp",
                             color="accent", h=26))
        cur = rd.load_defaults()
        self._inputs = {}
        for key, label in _FIELDS:
            row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(50),
                            spacing=dp(8))
            row.add_widget(_line(tr(label), size="15sp"))
            v = cur[key]
            ti = TextInput(text=f"{v:g}" if key in ("freq", "bw") else str(v),
                           multiline=False, size_hint=(None, None), width=dp(150),
                           height=dp(44), font_size="25sp",
                           input_filter="float" if key in ("freq", "bw") else "int")
            bind_field(ti, numeric=True)
            self._inputs[key] = ti
            row.add_widget(ti)
            col.add_widget(row)

        save = Button(text=tr("Save defaults"), size_hint_y=None, height=dp(54),
                      bold=True, font_size="18sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        save.bind(on_release=lambda *_: self._save())
        body.add_widget(col)
        self.add_widget(body)
        # OUTSIDE the scroll: at the end of the list it opened half hidden
        # under the bottom bar, caption clipped (glass, 2026-10-06)
        self.add_widget(save)
        self._status = _line("", size="13sp", color="green", h=24)
        self.add_widget(self._status)

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
        vals = self._read_fields()
        # Changing AWAY from the standard gets a strong warning first —
        # mismatched parameters silently split the mesh (operator spec
        # 2026-07-31). Saving standard values (or reverting) never nags.
        if not rd.is_standard(vals):
            self._confirm_nonstandard(vals)
            return
        self._commit(vals, tr("Saved"))

    def _commit(self, vals, verb):
        stored = rd.save_defaults(vals)
        self._fill_fields(stored)                    # reflect coercion
        self._status.color = theme.hex_to_rgba(theme.COLORS["green"])   # a new save starts clean (#24)
        self._status.text = tr("{verb} — BUILD pre-fills {summary}").format(
            verb=verb, summary=rd.summary(stored))
        try:                                          # home badge follows
            from kivy.app import App
            app = App.get_running_app()
            if app is not None and hasattr(app, "refresh_radio_badge"):
                app.refresh_radio_badge()
        except Exception:
            pass
        # Retune the medic's OWN radio to match (off-thread — it restarts
        # rnsd), otherwise the medic goes deaf to the nodes it now builds.
        import threading

        def _tune():
            try:
                from provisioning.medic_radio import retune_medic
                ok, msg = retune_medic(stored)
            except Exception as e:      # noqa: BLE001
                ok, msg = False, tr("Medic retune failed: {err}").format(
                    err=str(e)[:100])
            from kivy.clock import Clock

            def show(_dt):
                self._status.text += "   •  " + msg
                if not ok:
                    self._status.color = theme.hex_to_rgba(theme.COLORS["red"])
            Clock.schedule_once(show, 0)
        threading.Thread(target=_tune, daemon=True).start()

    def _confirm_nonstandard(self, vals):
        if getattr(self, "_ns_pop", None) is not None:   # doubled-tap guard
            return
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=tr(
            "[b]Keep the standard parameters?[/b]\n\n"
            "It is STRONGLY recommended to keep the standard settings\n"
            "[b]{standard}[/b]\n"
            "so that ALL nodes can communicate with each other.\n\n"
            "Nodes built with different parameters CANNOT hear the rest of "
            "the mesh. Only change this if every node you build will use the "
            "same new settings. Node Medic will retune its OWN radio to "
            "match, so it can still talk to your nodes.\n\nYou want to save:\n"
            "[b]{yours}[/b]").format(standard=rd.summary(rd.DEFAULT_PARAMS),
                                     yours=rd.summary(vals)),
            color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title=tr("Non-standard radio parameters"), content=box,
                      size_hint=(0.94, 0.8),
                      title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                      separator_color=theme.hex_to_rgba(theme.COLORS["red"]))
        self._ns_pop = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_ns_pop", None))
        keep = Button(text=tr("Keep standard"), bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _keep(*_):
            popup.dismiss()
            self._commit(dict(rd.DEFAULT_PARAMS), tr("Kept standard"))
        keep.bind(on_release=_keep)
        save_b = Button(text=tr("⚠  Save anyway"), bold=True, background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))

        def _save_anyway(*_):
            popup.dismiss()
            self._commit(vals, tr("Saved NON-STANDARD"))
        save_b.bind(on_release=_save_anyway)
        row.add_widget(save_b)                       # danger bottom-left
        row.add_widget(keep)                         # safe bottom-right
        box.add_widget(row)
        popup.open()

    def _confirm_preset(self, key):
        params = rd.preset_params(key)
        if params is None:
            return
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", text=(
            f"[b]{rd.preset_label(key)}[/b]\n\n{rd.summary(params)}\n\n"
            f"{rd.preset_note(key)}\n\n"
            + tr("Nodes built with these settings form a SEPARATE regional mesh "
                 "from nodes on a different band. Apply as the tool defaults?")),
            markup=True)
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        popup = Popup(title=tr("Apply regional preset"), content=box,
                      size_hint=(0.9, 0.6))
        cancel = Button(text=tr("Cancel"), background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        cancel.bind(on_release=popup.dismiss)
        apply_b = Button(text=tr("Apply"), bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _apply(*_):
            popup.dismiss()
            self._commit(params, tr("Applied {preset}").format(
                preset=rd.preset_label(key)))
        apply_b.bind(on_release=_apply)
        row.add_widget(cancel)
        row.add_widget(apply_b)
        box.add_widget(row)
        popup.open()
