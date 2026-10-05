"""Settings — the medic's config hub, reached from the gear on the home page.

For now it holds one entry (WiFi & Network); it's built as a menu so more settings
(radio defaults, display, about, …) drop in as rows without touching navigation.

# i18n: wrapped — the screen title, entry titles, section headers, subtitles
# and the body copy of every section are wrapped in tr(...). Runtime values go
# through .format() on a tr() template. See ui/i18n.py for how to wrap more.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.slider import Slider
from kivy.uix.switch import Switch
from kivy.uix.widget import Widget

from ui import theme
from ui.text_fit import grow_to_text
from ui.i18n import tr
from ui.widgets.slide_to_power import SlideToPowerOff
from provisioning.power import power_off
from provisioning import brightness as bright


def _line(text, bold=False, size="15sp", color="text_primary", h=30):
    lbl = Label(text=text, bold=bold, font_size=theme.font_sp(size),
                halign="left", valign="middle",
                size_hint_y=None,
                height=dp(max(h, theme.line_dp(size))),
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class SettingsScreen(BoxLayout):
    """A menu of settings. ``on_open(target)`` navigates to a setting's screen
    (e.g. ``"wifi"``)."""

    def __init__(self, on_open=None, on_retention_change=None,
                 node_count_provider=None, on_preview_screensaver=None,
                 on_home_profile_change=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = dp(10)
        self.padding = dp(16)
        self._on_open = on_open
        self._on_retention_change = on_retention_change
        self._node_count_provider = node_count_provider
        self._on_preview_screensaver = on_preview_screensaver
        self._on_home_profile_change = on_home_profile_change

        # The menu outgrew one screen (Home mode, Communication apps, the display
        # sections…), so it SCROLLS. Without this the top rows — including
        # Communication apps — clip off the top edge with no way to reach them.
        from kivy.uix.scrollview import ScrollView
        body = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10))
        body.bind(minimum_height=body.setter("height"))

        body.add_widget(_line(tr("Settings"), bold=True, size="24sp", h=44))
        body.add_widget(self._entry(tr("Language"), tr("Run Node Medic in your own language"), "language"))
        body.add_widget(self._entry(tr("Default radio parameters"), tr("Frequency, bandwidth, SF, CR, TX power that BUILD "
                                    "pre-fills — includes regional presets"), "radio_defaults"))
        body.add_widget(self._entry(tr("Tool identity"), tr("This medic's Reticulum identity, name, born date "
                                    "and lineage"), "tool_identity"))
        body.add_widget(self._entry(tr("Storage usage"), tr("SD card space and what's using it"), "storage"))
        body.add_widget(self._entry(tr("Trusted operators"), tr("Trust between cloned Node Medic units — the "
                                    "family tree"), "trusted_operators"))
        body.add_widget(self._entry(tr("Date & time"), tr("System clock and timezone — set manually or keep "
                                    "it synced from GPS"), "datetime"))
        body.add_widget(self._entry(tr("Wi-Fi & Network"), tr("Connect to a hotspot or venue Wi-Fi"), "wifi"))
        body.add_widget(self._entry(tr("Communication apps"), tr("Hand Columba or Sideband to a phone over Wi-Fi — "
                                    "the mesh messenger for your pocket"), "comms"))
        body.add_widget(self._entry(tr("Field readiness"), tr("Is this medic ready to be taken somewhere with "
                                    "no signal — firmware, apps, maps, wheels, "
                                    "toolchain"), "carry"))
        body.add_widget(self._home_mode_section())
        body.add_widget(self._brightness_section())
        body.add_widget(self._screensaver_section())
        body.add_widget(self._alerts_section())
        body.add_widget(self._entry(tr("Notifications"), tr("Get a message on your phone (Sideband/Columba) "
                                    "when a node needs checking — add your Reticulum "
                                    "address"), "notifications"))
        body.add_widget(self._retention_section())
        body.add_widget(self._entry(tr("Reticulum & radio guide"), tr("What RNode / transport / propagation nodes are, "
                                    "where to place them, and the radio settings"), "guide"))
        body.add_widget(self._entry(tr("Self Diagnose — this medic's radio & GPS"), tr("Check & heal this medic's OWN onboard radio + GPS "
                                    "board (11 live checks + auto-repairs)"), "self_diagnose"))
        # THE WAY BACK TO THE FIRST-USE WALKTHROUGH, and the way to hand this
        # medic on. It sits directly above the security preview because that is
        # what it mostly leads to, and its subtitle CHANGES when the security
        # half has not been done — an operator who tapped "not now" in a field
        # needs the medic to still be saying so when they get home, and one
        # more grey row in a list of twenty says nothing.
        body.add_widget(self._setup_entry())
        # THE REAL SWITCH, directly above the walkthrough of it. Its state
        # goes in a line of its own beneath, because _entry does not render
        # the subtitle argument (see _setup_entry) — and this is the row an
        # operator opens to find out whether their records are locked, so it
        # has to answer that before they tap it.
        body.add_widget(self._encryption_entry())
        from ui import setup_flow as _sf
        if _sf.SECURITY_HALF:
            # The lock-screen preview belongs to the walkthrough's security half
            # (off in v1, readiness ledger #174); a preview of a ceremony the
            # medic does not run would be a demo dressed as a feature.
            body.add_widget(self._entry(tr("Security preview  (walkthrough)"), tr("Walk the lock screen, recovery key and "
                                        "reset without changing anything"), "security_preview"))
        body.add_widget(self._entry(tr("About"), tr("Software version, test-suite status, uptime, "
                                    "and licence"), "about"))

        # Clean shutdown — a SLIDE (not a tap) so it can't fire by accident. Protects
        # the SD card from the hard-power-cut corruption risk (hit 2026-07-22).
        body.add_widget(_line(tr("Power"), bold=True, size="15sp", color="accent", h=28))
        self._power_slider = SlideToPowerOff(on_power_off=self._power_off)
        body.add_widget(self._power_slider)
        self._power_note = grow_to_text(_line("", size="12.5sp", color="text_secondary"))
        body.add_widget(self._power_note)

        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False, bar_width=dp(4))
        scroll.add_widget(body)
        self.add_widget(scroll)

    def _power_off(self):
        def do_off():
            def work():
                ok, msg = power_off()

                def land(_dt):
                    self._power_note.text = msg
                    if not ok:
                        try:
                            self._power_slider.reset()     # back to ON (ledger #36)
                        except Exception:                  # noqa: BLE001
                            pass
                Clock.schedule_once(land, 0)
            threading.Thread(target=work, daemon=True).start()

        # During a flash, WARN but let the operator override — a stuck flash must not
        # trap them into being unable to shut the medic down safely.
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is not None and app.flash_in_progress():
                from ui.confirm import confirm_power_override, flash_poweroff_warning
                confirm_power_override(flash_poweroff_warning(),
                                       tr("Flashing in progress"), do_off)
                return
        except Exception:
            pass
        do_off()

    # -- home mode (network role) -------------------------------------------
    def _home_mode_section(self):
        """Choose what HOME means (the front-page home/backpack toggle applies it):
        a full propagation node, or a plain transport node. Backpack always turns
        everything off, so this only affects the HOME state."""
        from workflows.node_mode import load_home_profile, PROPAGATION, TRANSPORT
        self._HP = (PROPAGATION, TRANSPORT)
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line(tr("Home mode"), bold=True, size="15sp", color="accent", h=26))
        box.add_widget(grow_to_text(_line(
            tr("What the medic does at HOME (the front-page toggle). Backpack always "
               "turns transport OFF so moving it can't disturb the mesh."),
            size="12.5sp", color="text_secondary")))
        current = load_home_profile()
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(52),
                        spacing=dp(8))
        self._hp_buttons = {}
        for val, label in ((PROPAGATION, tr("Full propagation node")),
                           (TRANSPORT, tr("Transport only"))):
            b = Button(text=label, bold=True, font_size="14.5sp",
                       background_normal="", background_down="")
            b.bind(on_release=lambda _b, v=val: self._set_home_profile(v))
            self._hp_buttons[val] = b
            row.add_widget(b)
        box.add_widget(row)
        self._hp_note = grow_to_text(_line("", size="12sp", color="text_secondary"))
        box.add_widget(self._hp_note)
        self._paint_home_profile(current)

        # Auto-backpack: if the medic's GPS shows it's moving, drop it out of
        # transport/propagation on its own so a roving unit can't disturb the mesh.
        from workflows.node_mode import load_auto_backpack, save_auto_backpack
        ab_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40),
                           spacing=dp(8))
        ab_row.add_widget(_line(tr("Auto-backpack when moving"), size="14sp", h=40))
        ab_sw = Switch(active=load_auto_backpack(), size_hint_x=None, width=dp(90))
        ab_sw.bind(active=lambda _s, v: save_auto_backpack(v))
        ab_row.add_widget(ab_sw)
        box.add_widget(ab_row)
        box.add_widget(grow_to_text(_line(
            tr("Uses the medic's GPS: when it senses it's on the move it switches to "
               "Backpack automatically. It never switches back on its own — tap the "
               "home icon to resume Home mode once you've settled."),
            size="12sp", color="text_secondary")))
        return box

    def _paint_home_profile(self, current):
        for val, b in self._hp_buttons.items():
            on = val == current
            b.background_color = theme.hex_to_rgba(
                theme.COLORS["green" if on else "surface"])
            b.color = theme.hex_to_rgba(
                theme.COLORS["background" if on else "text_primary"])
        self._hp_note.text = (
            tr("Propagation node: routes for the mesh AND stores messages for offline "
               "users (Columba/Sideband phones sync through it).")
            if current == self._HP[0] else
            tr("Transport node: routes for the mesh only — no message store-and-forward."))

    def _set_home_profile(self, value):
        from workflows.node_mode import save_home_profile
        saved = save_home_profile(value)
        self._paint_home_profile(saved)
        if self._on_home_profile_change:
            self._on_home_profile_change(saved)

    # -- display brightness -------------------------------------------------
    def _brightness_section(self):
        """A Display ▸ Brightness slider driving the touchscreen backlight. Shows a
        graceful note instead of a dead slider when the panel exposes no control."""
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line(tr("Display"), bold=True, size="15sp", color="accent", h=26))
        if not bright.has_control():
            box.add_widget(_line(tr("Brightness control isn't available on this display."),
                                 size="12.5sp", color="text_secondary", h=24))
            self._add_screen_fix_row(box)
            return box
        cur = bright.get_brightness()
        if cur is None:
            cur = bright.load_pct() or 80
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(48),
                        spacing=dp(10))
        lbl = _line(tr("Brightness"), size="15sp", h=48)
        lbl.size_hint_x, lbl.width = None, dp(104)
        row.add_widget(lbl)
        self._bright_slider = Slider(min=bright.MIN_PCT, max=100, value=cur, step=1)
        self._bright_slider.bind(value=lambda _i, v: self._on_brightness(v))
        row.add_widget(self._bright_slider)
        self._bright_val = _line(f"{int(cur)}%", size="14sp", h=48)
        self._bright_val.size_hint_x, self._bright_val.width = None, dp(52)
        row.add_widget(self._bright_val)
        box.add_widget(row)
        self._add_screen_fix_row(box)
        return box

    def _add_screen_fix_row(self, box):
        """Fix screen colours — a one-tap DSI panel re-init for the scrambled-
        panel state (shifted/wrong colours after a USB plug-in). Proven cure
        2026-08-25; see provisioning/screen_fix.py for the evidence trail. The
        screen goes black for ~2 s, then returns. Software cannot verify the
        panel, so the status line reports only that the re-init ran."""
        self._fix_btn = Button(
            text=tr("Fix screen colours"), size_hint_y=None, height=dp(44),
            background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self._fix_btn.bind(on_release=lambda *_: self._fix_screen())
        box.add_widget(self._fix_btn)
        self._fix_status = grow_to_text(_line(
            tr("If the screen shifts or shows wrong colours, this re-starts the "
               "panel (screen blanks ~2 s)."),
            size="12.5sp", color="text_secondary"))
        box.add_widget(self._fix_status)

    def _fix_screen(self):
        from provisioning import screen_fix
        self._fix_btn.disabled = True
        self._fix_status.text = tr("Re-initialising the panel…")

        def work():
            ok, msg = screen_fix.reinit_panel()
            def done(_dt):
                self._fix_btn.disabled = False
                self._fix_status.text = msg
            Clock.schedule_once(done, 0)

        threading.Thread(target=work, daemon=True).start()

    def _on_brightness(self, value):
        pct = int(value)
        self._bright_val.text = f"{pct}%"
        ev = getattr(self, "_bright_ev", None)
        if ev is not None:
            ev.cancel()                       # debounce: don't spam sudo while dragging
        self._bright_ev = Clock.schedule_once(lambda dt: self._apply_brightness(pct), 0.15)

    def _apply_brightness(self, pct):
        def work():
            ok, msg = bright.set_brightness(pct)
            if not ok:
                # the slider moved and nothing happened, silently — on any
                # panel but the developer's (readiness sweep, 2026-10-03)
                Clock.schedule_once(lambda dt: setattr(
                    self._fix_status, "text",
                    tr("Brightness isn't controllable on this display: ") + str(msg)), 0)
        threading.Thread(target=work, daemon=True).start()

    # -- screen saver -------------------------------------------------------
    def _screensaver_section(self):
        from provisioning import screensaver as ss
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line(tr("Screen saver"), bold=True, size="15sp",
                             color="accent", h=26))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                        spacing=dp(10))
        row.add_widget(_line(tr("Play when idle (protects the panel from burn-in)"),
                             size="14sp"))
        sw = Switch(active=ss.is_enabled(), size_hint_x=None, width=dp(90))
        sw.bind(active=lambda _i, v: ss.set_enabled(bool(v)))
        row.add_widget(sw)
        box.add_widget(row)
        box.add_widget(_line(tr("Style: {style}").format(
                                 style=ss.STYLE_LABELS.get(ss.style(), ss.style())),
                             size="12.5sp", color="text_secondary", h=20))
        self._ssv_delay = ss.idle_delay_s()
        drow = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(46),
                         spacing=dp(8))
        drow.add_widget(_line(tr("Start after"), size="14sp"))
        minus = Button(text="–", size_hint_x=None, width=dp(46), bold=True,
                       font_size="22sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        minus.bind(on_release=lambda *_: self._step_ssv(-1))
        self._ssv_lbl = _line(ss.format_delay(self._ssv_delay), size="16sp", h=46)
        self._ssv_lbl.halign = "center"
        self._ssv_lbl.size_hint_x, self._ssv_lbl.width = None, dp(90)
        plus = Button(text="+", size_hint_x=None, width=dp(46), bold=True,
                      font_size="22sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        plus.bind(on_release=lambda *_: self._step_ssv(+1))
        preview = Button(text=tr("See it now"), size_hint_x=None, width=dp(110), bold=True,
                         background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
        # Wide enough for its caption in every language (Russian ran past the
        # 110 dp box, 2026-10-05); never narrower than the English width.
        preview.bind(texture_size=lambda b, ts: setattr(b, "width", max(dp(110), ts[0] + dp(24))))
        preview.bind(on_release=lambda *_: (self._on_preview_screensaver
                                            and self._on_preview_screensaver()))
        drow.add_widget(minus)
        drow.add_widget(self._ssv_lbl)
        drow.add_widget(plus)
        drow.add_widget(preview)
        box.add_widget(drow)
        return box

    def _step_ssv(self, direction):
        from provisioning import screensaver as ss
        self._ssv_delay = ss.set_idle_delay_s(
            ss.step_idle(self._ssv_delay, direction))["idle_delay_s"]
        self._ssv_lbl.text = ss.format_delay(self._ssv_delay)

    # -- alerts -------------------------------------------------------------
    def _alerts_section(self):
        from monitor import alerts
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line(tr("Alerts"), bold=True, size="15sp", color="accent", h=26))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                        spacing=dp(10))
        row.add_widget(_line(tr("Alert me when a node goes orange or red"), size="14sp"))
        sw = Switch(active=alerts.is_enabled(), size_hint_x=None, width=dp(90))
        sw.bind(active=lambda _i, v: alerts.set_enabled(bool(v)))
        row.add_widget(sw)
        box.add_widget(row)
        box.add_widget(grow_to_text(_line(
            tr("Visual for now — a banner on VITALS and the affected nodes pushed to "
               "the top. (An audible option can be added later.)"),
            size="12sp", color="text_secondary")))
        return box

    # -- beacon history retention -------------------------------------------
    def _retention_section(self):
        from monitor import retention
        self._ret_days = retention.load_days()
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line(tr("Beacon history retention"), bold=True, size="15sp",
                             color="accent", h=26))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(46),
                        spacing=dp(8))
        minus = Button(text="–", size_hint_x=None, width=dp(52), bold=True,
                       font_size="22sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        minus.bind(on_release=lambda *_: self._step_retention(-1))
        self._ret_lbl = _line("", size="17sp", h=46)
        self._ret_lbl.halign = "center"
        plus = Button(text="+", size_hint_x=None, width=dp(52), bold=True,
                      font_size="22sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        plus.bind(on_release=lambda *_: self._step_retention(+1))
        row.add_widget(minus)
        row.add_widget(self._ret_lbl)
        row.add_widget(plus)
        box.add_widget(row)
        self._ret_impact = _line("", size="12.5sp", color="text_secondary", h=22)
        box.add_widget(self._ret_impact)
        self._refresh_retention()
        return box

    def _step_retention(self, direction):
        from monitor import retention
        self._ret_days = retention.set_days(
            retention.step(self._ret_days, direction))
        if self._on_retention_change:
            self._on_retention_change(self._ret_days)
        self._refresh_retention()

    def _refresh_retention(self):
        from monitor import retention
        self._ret_lbl.text = tr("{n} days").format(n=self._ret_days)
        n = self._node_count_provider() if self._node_count_provider else 0
        est = retention.estimate_bytes(self._ret_days, max(n, 1))
        impact = (tr("Storage impact: ≈ {size} for {n} node (estimate)") if n == 1
                  else tr("Storage impact: ≈ {size} for {n} nodes (estimate)"))
        self._ret_impact.text = impact.format(size=retention.format_size(est), n=n)

    def _setup_entry(self):
        """The first-use walkthrough, re-runnable — with a line under it while
        it is still owed.

        Two separate people need this row. The operator who skipped the security
        part to get on with a repair, who has to be able to come back to it; and
        whoever is handed this medic next, who needs it to introduce itself from
        nothing ([[networks-outlast-builders]] — a tool is only as durable as
        the next person's ability to pick it up).

        The subtitle argument every other row passes is not rendered by
        ``_entry`` and never has been, so this row states its outstanding status
        in a line of its own underneath, the way the sections in this file
        already do. Colour alone would not have said it: one amber row in a list
        of twenty reads as styling.

        Best-effort. A Settings row must not be the thing that fails to draw
        because a marker file could not be read.
        """
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        box.bind(minimum_height=box.setter("height"))
        from ui import setup_flow as _sf
        box.add_widget(self._entry(
            tr("Set up this Node Medic"),
            tr("The security setup, and what each mode is for") if _sf.SECURITY_HALF
            else tr("What each front-page card is for"), "setup"))
        try:
            from provisioning.first_use import security_outstanding
            outstanding = _sf.SECURITY_HALF and security_outstanding()
        except Exception:
            outstanding = False
        if outstanding:
            box.add_widget(_line(
                tr("Nobody has chosen how this medic locks its own records yet."),
                size="12.5sp", color="warning_yellow",
                h=theme.line_dp("12.5sp")))
        return box

    def _encryption_entry(self):
        """The encrypt-at-rest switch, with its live state underneath.

        Built fresh by ``refresh_encryption_row`` every time Settings is opened.
        The body of this screen is assembled once in __init__, and the operator
        reaches this row IMMEDIATELY after changing the thing it reports — a row
        still reading "NOT encrypted" over freshly encrypted records is the
        stale-state failure this project keeps finding (the wizard's
        vault_exists_fn defaulted to False for the same reason).

        Best-effort: a Settings row must never be the thing that fails to draw.
        """
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(self._entry(tr("Encrypt my records"), tr("Lock the registry, certificates and "
                                   "messages on this card"), "encryption"))
        try:
            from provisioning import encryption_flow as ef
            st = ef.state()
            on, doors = bool(st["on"]), len(st["doors"])
        except Exception:
            # COULD NOT CHECK is not "encrypted", and it is not a blank row
            # either — say which it is.
            box.add_widget(_line(tr("Could not check whether your records are "
                                    "encrypted."), size="12.5sp",
                                 color="warning_yellow",
                                 h=theme.line_dp("12.5sp")))
            return box
        if on:
            encrypted = (tr("Encrypted — {n} key opens them.") if doors == 1
                         else tr("Encrypted — {n} keys open them."))
            box.add_widget(_line(
                encrypted.format(n=doors), size="12.5sp", color="green",
                h=theme.line_dp("12.5sp")))
        else:
            box.add_widget(_line(
                tr("Not encrypted — anyone who takes this card can read them."),
                size="12.5sp", color="warning_yellow",
                h=theme.line_dp("12.5sp")))
        self._encryption_box = box
        return box

    def refresh_encryption_row(self):
        """Redraw the row's state line. Called when Settings is opened."""
        box = getattr(self, "_encryption_box", None)
        parent = box.parent if box is not None else None
        if parent is None:
            return
        index = parent.children.index(box)
        parent.remove_widget(box)
        parent.add_widget(self._encryption_entry(), index=index)

    def _entry(self, title, subtitle, target):
        # THE SUBTITLE IS DRAWN. Every row passed one and none was rendered,
        # so a stranger saw sixteen bold titles and no idea what "Tool
        # identity" or "Field readiness" did (readiness sweep, 2026-10-03).
        sec = theme.COLORS["text_secondary"].lstrip("#")
        text = (f"[b]{title}[/b]\n[size=13sp][color={sec}]{subtitle}[/color][/size]"
                if subtitle else title)
        row = Button(text=text, markup=True, size_hint_y=None,
                     height=dp(74) if subtitle else dp(62), halign="left",
                     valign="middle", font_size="18sp",
                     background_normal="", background_down="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        row.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(24), v[1])))
        row.bind(on_release=lambda *_: self._on_open and self._on_open(target))
        return row
