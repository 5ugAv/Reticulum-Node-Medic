"""Guided birth — one instruction per screen, for a first-time operator.

Instead of one dense form, BIRTH can be walked through step by step: pick what
you're building, then follow a screen per action (plug the board in, insert the
SD card, …) with a simple animation showing the motion. The physical-prep steps
live here; once the hardware is connected the guide hands off to the existing
BIRTH screen (``on_complete``) which does the detect / name / flash work.

The step LISTS are pure data (``guide_steps``) so the ordering is unit-testable
without Kivy; the screen is just the presentation over them.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label

from ui import theme
from ui.i18n import tr  # i18n: wrapped — guided-birth screen labels/buttons
from ui.birth_guide_flow import ANTENNA_STEP, BIRTH_PATHS, guide_steps
from ui.widgets.wizard_step import WizardStep
from ui.widgets.birth_anims import (ConnectAntennaAnim, ConnectBoardAnim,
                                    InsertSdAnim, ProvisionAnim)

#: Animation key (from ui.birth_guide_flow) -> the widget class that draws it.
_ANIMS = {"connect_antenna": ConnectAntennaAnim, "connect_board": ConnectBoardAnim,
          "insert_sd": InsertSdAnim, "provision": ProvisionAnim}


def _line(text, size, color="text_primary", bold=False, h=None):
    lbl = Label(text=text, font_size=size, bold=bold, halign="left", valign="middle",
                color=theme.hex_to_rgba(theme.COLORS[color]))
    if h is not None:
        lbl.size_hint_y = None
        lbl.height = dp(h)
    lbl.bind(size=lambda i, v: setattr(i, "text_size", v))
    return lbl


class BirthGuideScreen(BoxLayout):
    """The step-by-step birth walkthrough. ``on_complete(path)`` fires when the
    physical-prep steps are done, to hand off to the real BIRTH flow."""

    def __init__(self, on_complete=None, on_navigate=None, heard_fn=None,
                 adopt_air_fn=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self._on_complete = on_complete
        self._on_navigate = on_navigate       # (screen_name) -> switch to a screen
        self._heard_fn = heard_fn             # () -> [candidate dicts heard over mesh]
        self._adopt_air_fn = adopt_air_fn     # (key,name,type,board,fw) -> enroll kin
        self._path = None
        self._i = 0
        self._current = None
        self._node_name = ""
        self.reset()

    def reset(self):
        """Re-entered: back to the antenna-first landing (attach the antenna BEFORE
        the next screen powers the board over USB), which leads to the detect-first
        landing (plug a node in; the medic decides BIRTH vs ADOPT)."""
        self._stop_current()
        self._path = None
        self._i = 0
        self._node_name = ""
        self._render_antenna()

    # -- antenna-first landing (before any board is powered) ---------------
    def _render_antenna(self):
        """The very first BIRTH screen: attach an antenna to the radio board, with
        the no-antenna damage warning — shown BEFORE detect, since plugging the
        board into USB there powers it, and powering a radio with no antenna can
        fry it. One landing covers every path (all reach detect)."""
        self._stop_current()
        self.clear_widgets()
        self._back_action = None          # antenna landing is the root -> home
        anim = ConnectAntennaAnim()
        step = WizardStep(
            index=0, total=1, title=ANTENNA_STEP["title"], body=ANTENNA_STEP["body"],
            anim=anim, hint=ANTENNA_STEP.get("hint", ""),
            warning=ANTENNA_STEP["warning"], next_text=tr("Antenna on  →"),
            on_next=self._render_detect,
            on_back=lambda: self._on_navigate and self._on_navigate("home"))
        self.add_widget(step)
        self._current = step
        step.start()

    def handle_back(self):
        """Left-edge swipe: step back ONE page within the flow. Returns True if it
        stepped back; False at the root (the detect landing) so the app goes home.
        Each _render_* sets ``self._back_action`` to its previous page (or None)."""
        action = getattr(self, "_back_action", None)
        if callable(action):
            action()
            return True
        return False

    # -- detect-first landing ---------------------------------------------
    def _render_detect(self):
        """Plug a node in; the medic reads it and routes to ADOPT (already ours)
        or BIRTH (fresh/foreign). 'Choose manually' opens the full chooser (also
        the home for Pi + Mitosis, which aren't plug-in-a-radio-board cases)."""
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_antenna   # back -> the antenna landing
        anim = ConnectBoardAnim()
        step = WizardStep(
            index=0, total=1, title=tr("Connect your node"),
            body=tr("Plug the node into Node Medic with a USB data cable. I'll detect "
                    "it and decide whether to build it or adopt it as kin."),
            anim=anim,
            hint=tr("Use a DATA USB cable — a charge-only cable won't be seen."),
            next_text=tr("Choose manually  →"), on_next=self._render_intro,
            on_back=self._render_antenna)
        self.add_widget(step)
        self._current = step
        step.start()
        # 'Choose manually' is an ADVANCED escape, not the primary action (the
        # primary path is just plugging a node in) — muted khaki-green so it doesn't
        # invite like the usual green Next.
        step.next_btn.background_color = theme.hex_to_rgba("#78866b")
        step.next_btn.color = theme.hex_to_rgba("#f0f0f0")
        self._start_board_poll(anim, on_present=self._on_detect)

    def _on_detect(self, anim):
        """A board appeared — celebrate, then read + classify it off-thread."""
        self._stop_board_poll()
        if hasattr(anim, "mark_connected"):
            anim.mark_connected()
        from kivy.clock import Clock
        Clock.schedule_once(lambda _d: self._render_reading(), 1.6)

    def _render_reading(self):
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_detect   # reading -> re-detect
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(18))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Reading the board…"), "24sp", bold=True, h=40))
        wrap.add_widget(_line(tr("Checking whether it's already one of ours "
                                 "(this resets the board briefly)."),
                              "16sp", color="text_secondary", h=60))
        wrap.add_widget(Widget())
        self.add_widget(wrap)
        import threading

        def work():
            c = {"kind": "birth", "reason": "Couldn't read the board."}
            try:
                from ui.hw_factories import local_board_ports
                from ui.adopt_live import read_board_banner, read_status_via_mdns
                from monitor.node_classifier import classify
                ports = local_board_ports()
                port = ports[0] if ports else None
                status = read_status_via_mdns(port)      # name, pre-reset
                banner = read_board_banner(port)         # identity + params
                c = classify(banner, status)
                c["_port"] = port
            except Exception as e:      # noqa: BLE001
                c = {"kind": "birth", "reason": f"Couldn't read the board: {e}"}
            from kivy.clock import Clock
            Clock.schedule_once(lambda _d: self._route(c), 0)
        threading.Thread(target=work, daemon=True).start()

    def _route(self, c):
        from ui.adopt_live import is_kin
        if is_kin(c.get("identity_hash")):
            self._render_already_kin(c)   # already one of ours -> nothing to do
        elif c.get("kind") == "adopt":
            self._render_adopt(c)
        else:
            self._render_intro()          # birth -> the build chooser

    def _render_already_kin(self, c):
        self._stop_current()
        self.clear_widgets()
        from ui.adopt_live import known_name
        name = c.get("node_name") or known_name(c.get("identity_hash")) or tr("This node")
        self._back_action = self._render_detect   # already-kin info -> re-detect
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(14))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Already kin"), "28sp", bold=True, h=44, color="green"))
        wrap.add_widget(_line(tr("{name} is already one of your kin — it's enrolled "
                                 "and reporting to VITALS. Nothing to do.").format(name=name),
                              "16sp", color="text_secondary", h=80))
        wrap.add_widget(_line(tr("Identity")
                              + f"  {(c.get('identity_hash') or '')[:16]}…",
                              "12.5sp", color="text_secondary", h=22))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(58),
                        spacing=dp(12))
        vit = Button(text=tr("See in VITALS"), bold=True, font_size="16sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        vit.bind(on_release=lambda *_: self._on_navigate and self._on_navigate("vitals"))
        done = Button(text=tr("Done"), bold=True, font_size="16sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        done.bind(on_release=lambda *_: self._on_navigate and self._on_navigate("home"))
        row.add_widget(vit)
        row.add_widget(done)
        wrap.add_widget(row)
        # REBIRTH (operator request 2026-07-31): wipe + flash fresh — the
        # deliberate path for a RENAME or a hard reset of a misbehaving node.
        reb = Button(text=tr("Rebirth — wipe this node & build it fresh"),
                     size_hint_y=None, height=dp(46), font_size="14sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["amber"]))
        reb.bind(on_release=lambda *_: self._confirm_rebirth(c))
        wrap.add_widget(reb)
        wrap.add_widget(Widget())
        self.add_widget(wrap)

    def _confirm_rebirth(self, c):
        """Destructive-action gate: rebirth erases the board completely — new
        identity, config gone, VITALS history detaches from the old identity."""
        from kivy.uix.popup import Popup
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))
        body.add_widget(_line(tr("This ERASES the node completely:"), "16sp",
                              bold=True, color="amber", h=28))
        body.add_widget(_line(tr("• its identity is wiped — it becomes a brand-new "
                                 "node (old history detaches)\n• its name, WiFi and "
                                 "radio settings are wiped\n• then the normal birth "
                                 "runs: flash, name it, auto-setup"),
                              "14sp", color="text_secondary", h=96))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(54),
                        spacing=dp(10))
        go = Button(text=tr("⚠  Wipe & rebirth"), bold=True, font_size="15sp",
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel = Button(text=tr("Cancel"), bold=True, font_size="15sp",
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
        row.add_widget(go)
        row.add_widget(cancel)
        body.add_widget(row)
        pop = Popup(title=tr("Rebirth this node?"), content=body,
                    size_hint=(0.9, 0.55),
                    title_color=theme.hex_to_rgba(theme.COLORS["amber"]))
        cancel.bind(on_release=lambda *_: pop.dismiss())
        go.bind(on_release=lambda *_: (pop.dismiss(), self._do_rebirth(c)))
        pop.open()

    def _do_rebirth(self, c):
        """Erase the board (guard-checked), forget its old roster identity, and
        hand off to the normal (proven single-pass) birth flow."""
        port = c.get("_port")
        old_ident = c.get("identity_hash") or ""
        old_name = c.get("node_name") or ""
        self._stop_current()
        self.clear_widgets()
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(14))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Wiping the board…"), "24sp", bold=True, h=40))
        wrap.add_widget(_line(tr("A few seconds — then the normal birth starts."),
                              "14sp", color="text_secondary", h=24))
        wrap.add_widget(Widget())
        self.add_widget(wrap)

        def work():
            ok, msg = True, ""
            try:
                from ui.onboard_roster import assert_flashable
                assert_flashable(port)               # NEVER the medic's own radio
                import glob
                import os
                import subprocess
                et = (glob.glob(os.path.expanduser(
                    "~/.platformio/packages/tool-esptoolpy/esptool.py")) or [None])[0]
                if not (port and et):
                    ok, msg = False, tr("Couldn't find the board or the flash tool.")
                else:
                    r = subprocess.run(
                        ["python3", et, "--port", port, "erase_flash"],
                        capture_output=True, text=True, timeout=120)
                    ok = r.returncode == 0
                    if not ok:
                        msg = (r.stderr or r.stdout or "").strip()[-160:]
                if ok and old_ident:
                    try:                              # forget the old identity
                        from monitor import kin_roster
                        ro = kin_roster.load_roster()
                        if old_ident in ro:
                            del ro[old_ident]
                            kin_roster._save(ro, kin_roster.KIN_ROSTER_PATH)
                    except Exception:
                        pass
            except Exception as e:                    # noqa: BLE001
                ok, msg = False, str(e)[:160]
            from kivy.clock import Clock

            def done(_dt):
                if ok:
                    # blank board -> the proven birth flow takes over; old name
                    # prefilled as a starting point (rename freely).
                    if self._on_complete:
                        self._on_complete("radio", old_name)
                else:
                    from ui.requirement_popup import requirement_popup
                    requirement_popup(
                        tr("Couldn't wipe the board: ") + (msg or tr("unknown")),
                        tr("Rebirth failed"), False)
                    self._render_detect()
            Clock.schedule_once(done, 0)
        import threading
        threading.Thread(target=work, daemon=True).start()

    # -- adopt confirm / run ----------------------------------------------
    def _render_adopt(self, c):
        self._stop_current()
        self.clear_widgets()
        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self._back_action = self._render_detect   # adopt confirm -> re-detect
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        wrap.add_widget(_line(tr("Existing node found"), "24sp", bold=True, h=36))
        wrap.add_widget(_line(tr("This node is already running our config — adopt it "
                                 "as kin (no flashing, keeps its settings)."),
                              "15sp", color="text_secondary", h=48))
        # Adoption INHERITS the node's own name (it doesn't reflash, so it can't
        # rename the node — 'Re-birth instead' is the deliberate rename path). Show
        # it read-only when we can read it (from /status or the medic's records);
        # only offer a field as a fallback when the name is genuinely unknown.
        from ui.adopt_live import known_name
        name0 = c.get("node_name") or known_name(c.get("identity_hash")) or ""
        wrap.add_widget(_line(tr("Name (kept from the node)"), "13sp",
                              color="accent", h=20))
        if name0:
            wrap.add_widget(_line(name0, "20sp", bold=True, h=32))
            self._adopt_name = None
            self._adopt_name_value = name0
        else:
            ti = TextInput(hint_text=tr("Couldn't read the node's name — enter one"),
                           multiline=False, size_hint_y=None, height=dp(56),
                           font_size="19sp")
            bind_field(ti)
            self._adopt_name = ti
            self._adopt_name_value = ""
        p = c.get("params") or {}
        det = (f"Board:  {c.get('board') or '—'}      Firmware:  {c.get('firmware') or '—'}\n"
               f"Radio:  {(p.get('freq', 0) / 1e6):.3f} MHz   SF{p.get('sf')}   "
               f"{int(p.get('bw', 0) / 1000)}k   CR{p.get('cr')}   {p.get('txp')} dBm   [OK]\n"
               f"Identity:  {(c.get('identity_hash') or '')[:16]}…   Beaconing [OK]")
        wrap.add_widget(_line(det, "13.5sp", color="text_secondary", h=78))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62),
                        spacing=dp(12))
        reb = Button(text=tr("Re-birth instead"), font_size="16sp", bold=True,
                     background_normal="", size_hint_x=0.42,
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        reb.bind(on_release=lambda *_: self._render_intro())
        adopt = Button(text=tr("Adopt as kin"), font_size="19sp", bold=True,
                       background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        adopt.bind(on_release=lambda *_: self._do_adopt(c))
        row.add_widget(reb)
        row.add_widget(adopt)
        wrap.add_widget(row)
        self.add_widget(wrap)

    def _do_adopt(self, c):
        # GATE: the medic is AT the node for a USB adopt, so confirm its GPS on a
        # map before it's baked into the cert (catches a wrong/stale fix).
        name = ((self._adopt_name.text or "").strip() if self._adopt_name
                else self._adopt_name_value)
        fix = None
        try:
            from monitor.geo import read_gps
            f = read_gps()
            fix = (f.lat, f.lon) if f else None
        except Exception:
            fix = None
        if fix is None:
            self._run_adopt(c, name, None)
            return
        try:
            from ui.widgets.confirm_location import ConfirmLocationPopup
            from monitor.geo import splitter_gps_reader
            ConfirmLocationPopup(
                fix[0], fix[1], node_name=name or c.get("name", ""),
                on_confirm=lambda lat, lon: self._run_adopt(c, name, (lat, lon)),
                on_cancel=lambda: self._run_adopt(c, name, None),
                gps_reader=splitter_gps_reader()).open()
        except Exception:
            self._run_adopt(c, name, fix)

    def _run_adopt(self, c, name, location):
        self._stop_current()
        self.clear_widgets()
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Adopting…"), "24sp", bold=True, h=40))
        self._adopt_status = _line(tr("Reading identity, writing certificate, "
                                      "enrolling as kin…"), "15sp",
                                   color="text_secondary", h=60)
        wrap.add_widget(self._adopt_status)
        wrap.add_widget(Widget())
        self.add_widget(wrap)
        import threading

        def work():
            ok, msg = False, "Adoption failed."
            try:
                from ui.adopt_live import make_adopt_workflow
                wf = make_adopt_workflow(board_port=c.get("_port"),
                                         name_override=name, location=location)
                wf.run_all()
                ok = wf.succeeded
                msg = wf.results[-1].message if wf.results else msg
            except Exception as e:      # noqa: BLE001
                ok, msg = False, f"Adoption failed: {e}"
            from kivy.clock import Clock
            Clock.schedule_once(lambda _d: self._render_adopt_done(ok, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _render_adopt_done(self, ok, msg):
        self._stop_current()
        self.clear_widgets()
        self._back_action = None          # terminal outcome -> home
        wrap = BoxLayout(orientation="vertical", padding=dp(24), spacing=dp(16))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        wrap.add_widget(_line(tr("Adopted [OK]") if ok else tr("Couldn't adopt"),
                              "26sp", bold=True, h=42,
                              color="green" if ok else "warning_yellow"))
        wrap.add_widget(_line(msg, "16sp", color="text_secondary", h=80))
        done = Button(text=tr("Done"), size_hint_y=None, height=dp(58), bold=True,
                      font_size="18sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        done.bind(on_release=lambda *_: (self._on_navigate and
                                         self._on_navigate("vitals" if ok else "home")))
        wrap.add_widget(done)
        wrap.add_widget(Widget())
        self.add_widget(wrap)

    # -- rendering ---------------------------------------------------------
    def _render_intro(self):
        self.clear_widgets()
        self._current = None
        self._back_action = self._render_detect   # chooser -> detect landing
        wrap = BoxLayout(orientation="vertical", padding=dp(22), spacing=dp(16))
        from ui.widgets.help_button import HelpButton
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                         spacing=dp(8))
        head.add_widget(_line(tr("What are you building?"), "26sp", bold=True))
        head.add_widget(HelpButton())
        wrap.add_widget(head)
        wrap.add_widget(_line(tr("Not sure which is which? Tap the  ?  above. Node Medic "
                                 "will guide you the rest of the way."),
                              "16sp", color="text_secondary", h=56))
        for key, title, subtitle in BIRTH_PATHS:
            # the Pi card carries a longer description — give it room so it doesn't
            # clip; the shorter cards stay compact.
            h = 170 if key == "pi" else 104
            wrap.add_widget(self._path_button(key, title, subtitle, height=h))
        # Mitosis is a different KIND of action — not building a node but cloning
        # the Node Medic itself — so it sits at the end, styled apart, and routes
        # straight to the MITOSIS screen (no guided build steps).
        wrap.add_widget(self._mitosis_button())
        # Adopt a node the medic HEARS over LoRa (no USB) — field fleet enrolment.
        if self._heard_fn is not None:
            wrap.add_widget(self._over_air_button())
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        self.add_widget(wrap)

    def _over_air_button(self):
        btn = Button(size_hint_y=None, height=dp(104), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["green"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line(tr("Adopt over the air (LoRa)"), "21sp", bold=True,
                               color="background", h=30))
        inner.add_widget(_line(tr("Enrol a node you can hear on the mesh as kin - no "
                                  "cable needed. For nodes already in the field."),
                               "14sp", color="background"))
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._render_over_air_list())
        return btn

    # -- over-the-air adoption --------------------------------------------
    def _render_over_air_list(self):
        self._stop_current()
        self.clear_widgets()
        self._back_action = self._render_intro    # heard list -> chooser
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.widget import Widget
        root = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(8))
        head = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(40),
                         spacing=dp(8))
        head.add_widget(_line(tr("Nodes heard on the mesh"), "22sp", bold=True))
        back = Button(text=tr("←  Back"), size_hint_x=None, width=dp(96),
                      font_size="14sp", background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        back.bind(on_release=lambda *_: self._render_intro())
        head.add_widget(back)
        root.add_widget(head)
        root.add_widget(_line(tr("Pick one to adopt as kin over LoRa (no cable)."),
                              "14sp", color="text_secondary", h=26))
        cands = []
        try:
            cands = self._heard_fn() or []
        except Exception:
            cands = []
        sv = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        col.bind(minimum_height=col.setter("height"))
        if not cands:
            col.add_widget(_line(tr("Nothing heard yet — the medic hasn't received a "
                                    "beacon/announce. Give it a moment on the mesh."),
                                 "14sp", color="text_secondary", h=60))
        for c in cands:
            col.add_widget(self._heard_row(c))
        sv.add_widget(col)
        root.add_widget(sv)
        self.add_widget(root)

    def _heard_row(self, c):
        is_kin = c.get("provenance") == "kin"
        btn = Button(size_hint_y=None, height=dp(84), background_normal="",
                     background_color=theme.hex_to_rgba(
                         theme.COLORS["surface" if not is_kin else "background"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(14), dp(8)], spacing=dp(2))
        tag = ("  " + tr("(already kin)")) if is_kin else ""
        inner.add_widget(_line(f"{c.get('name', tr('(unnamed)'))}{tag}", "18sp",
                               bold=True, h=26))
        lsh = c.get("last_seen_hours")
        seen = (tr("heard {h}h ago").format(h=f"{lsh:.1f}")
                if isinstance(lsh, (int, float)) else tr("heard"))
        sig = c.get("signal_dbm")
        sigs = f" · {sig} dBm" if sig is not None else ""
        inner.add_widget(_line(f"{c.get('node_type', 'node')} · {seen}{sigs}",
                               "13sp", color="text_secondary"))
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._render_over_air_confirm(c))
        return btn

    def _render_over_air_confirm(self, c):
        self._stop_current()
        self.clear_widgets()
        from kivy.uix.textinput import TextInput
        from ui.onscreen_keyboard import bind_field
        self._back_action = self._render_over_air_list
        wrap = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        wrap.add_widget(_line(tr("Adopt over LoRa"), "24sp", bold=True, h=36))
        wrap.add_widget(_line(tr("Enrol this node as kin from its mesh beacon — no "
                                 "cable, keeps its settings."), "15sp",
                              color="text_secondary", h=44))
        ti = TextInput(text=c.get("name") or "", multiline=False,
                       hint_text=tr("Node name"), size_hint_y=None, height=dp(56),
                       font_size="19sp")
        bind_field(ti)
        self._air_name = ti
        wrap.add_widget(_line(tr("Name"), "13sp", color="accent", h=20))
        wrap.add_widget(ti)
        det = (f"Type:  {c.get('node_type', 'node')}      "
               f"Board:  {c.get('board') or '—'}      Firmware:  {c.get('firmware') or '—'}\n"
               f"Identity:  {(c.get('key') or '')[:16]}…   Heard on the mesh [OK]")
        wrap.add_widget(_line(det, "13.5sp", color="text_secondary", h=56))
        from kivy.uix.widget import Widget
        wrap.add_widget(Widget())
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62),
                        spacing=dp(12))
        cancel = Button(text=tr("Back"), font_size="16sp", bold=True, size_hint_x=0.4,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel.bind(on_release=lambda *_: self._render_over_air_list())
        adopt = Button(text=tr("Adopt as kin"), font_size="19sp", bold=True,
                       background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                       color=theme.hex_to_rgba(theme.COLORS["background"]))
        adopt.bind(on_release=lambda *_: self._do_over_air(c))
        row.add_widget(cancel)
        row.add_widget(adopt)
        wrap.add_widget(row)
        self.add_widget(wrap)

    def _do_over_air(self, c):
        name = (self._air_name.text or "").strip() or c.get("name") or "node"
        # PLACEMENT: the medic can't sense a remote node, so the operator drops it
        # on the map. Start on its known location (if kin) or the medic's position.
        start = None
        try:
            from monitor.kin_roster import load_roster
            e = load_roster().get(c.get("key")) or {}
            if e.get("lat") is not None:
                start = (e["lat"], e["lon"])
        except Exception:
            start = None
        if start is None:
            try:
                from monitor.geo import read_gps
                f = read_gps()
                start = (f.lat, f.lon) if f else None
            except Exception:
                start = None
        if start is None:
            self._run_over_air(c, name, None)     # nowhere to centre a map
            return
        try:
            from ui.widgets.confirm_location import ConfirmLocationPopup
            from monitor.geo import splitter_gps_reader
            ConfirmLocationPopup(
                start[0], start[1], node_name=name,
                on_confirm=lambda lat, lon: self._run_over_air(c, name, (lat, lon)),
                on_cancel=lambda: self._run_over_air(c, name, None),
                gps_reader=splitter_gps_reader()).open()
        except Exception:
            self._run_over_air(c, name, start)

    def _run_over_air(self, c, name, location):
        ok, msg = False, "Adoption failed."
        try:
            if self._adopt_air_fn is not None:
                self._adopt_air_fn(c.get("key"), name, c.get("node_type", "rtnode2400"),
                                   c.get("board"), c.get("firmware"), location)
                ok, msg = True, f"{name} adopted as kin over LoRa — now in VITALS."
        except Exception as e:      # noqa: BLE001
            ok, msg = False, f"Adoption failed: {e}"
        self._render_adopt_done(ok, msg)

    def _mitosis_button(self):
        btn = Button(size_hint_y=None, height=dp(104), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["accent"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line(tr("Mitosis - clone this Node Medic"), "21sp",
                               bold=True, color="background", h=30))
        inner.add_widget(_line(tr("Copy this Node Medic onto a fresh Raspberry Pi 5 - "
                                  "a second building tool."), "14sp", color="background"))
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._on_navigate and self._on_navigate("mitosis"))
        return btn

    def _path_button(self, key, title, subtitle, height=104):
        btn = Button(size_hint_y=None, height=dp(height), background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]))
        inner = BoxLayout(orientation="vertical", padding=[dp(18), dp(12)], spacing=dp(4))
        inner.add_widget(_line(title, "21sp", bold=True, h=30))
        sub = _line(subtitle, "14sp", color="text_secondary")
        inner.add_widget(sub)
        inner.size = btn.size
        btn.bind(size=lambda _b, v: setattr(inner, "size", v),
                 pos=lambda _b, v: setattr(inner, "pos", v))
        btn.add_widget(inner)
        btn.bind(on_release=lambda *_: self._choose(key))
        return btn

    def _choose(self, path):
        self._path = path
        self._i = 0
        self._render_name()

    def _render_name(self):
        """First guided step: name the node. Folded into the flow here (instead of
        on the BIRTH screen) so the whole birth is one continuous walkthrough; the
        name is carried to the BIRTH hand-off prefilled."""
        self._stop_current()
        from kivy.uix.textinput import TextInput
        from kivy.clock import Clock
        from ui.onscreen_keyboard import bind_field
        self._back_action = self._render_intro    # name step -> the chooser
        total = len(guide_steps(self._path)) + 1
        ti = TextInput(text=self._node_name, multiline=False,
                       hint_text=tr("Name this node  (e.g. Rooftop-East)"),
                       size_hint_y=None, height=dp(58), font_size="20sp")
        bind_field(ti)
        self._name_input = ti
        step = WizardStep(index=0, total=total, title=tr("Name this node"),
                          body=tr("Give this node a short, memorable name — you'll see it "
                                  "on the map and on its birth certificate."),
                          input_widget=ti, next_text=tr("Next  →"),
                          on_next=self._name_next, on_back=self.reset)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step
        Clock.schedule_once(lambda *_: setattr(ti, "focus", True), 0.3)

    def _name_next(self):
        name = (self._name_input.text or "").strip()
        if not name:                         # a name is required to continue
            self._name_input.focus = True
            return
        self._node_name = name
        self._i = 0
        self._render_step()

    def _render_step(self):
        steps = guide_steps(self._path)
        if not steps or self._i >= len(steps):
            self._finish()
            return
        self._stop_current()
        self._back_action = self._back        # guided step -> previous step / name
        s = steps[self._i]
        anim_cls = _ANIMS.get(s.get("anim"))
        anim = anim_cls() if anim_cls else None
        # +1 on index/total for the name step folded in ahead of these
        step = WizardStep(index=self._i + 1, total=len(steps) + 1, title=s["title"],
                          body=s["body"], anim=anim, hint=s.get("hint", ""),
                          warning=s.get("warning", ""),
                          next_text=s.get("next", "Next  →"),
                          on_next=self._next, on_back=self._back)
        self.clear_widgets()
        self.add_widget(step)
        self._current = step
        step.start()
        # A "connect your board" step loops until the medic SENSES a board on USB,
        # then the animation fires its green "Connected!" burst. Until then Next is
        # grayed out — you can't move on without a board actually plugged in.
        if isinstance(anim, ConnectBoardAnim):
            step.set_next_enabled(False)
            self._start_board_poll(anim)

    # -- navigation --------------------------------------------------------
    def _next(self):
        self._advance_token = getattr(self, "_advance_token", 0) + 1   # cancel auto-advance
        steps = guide_steps(self._path)
        cur = steps[self._i] if self._i < len(steps) else {}
        if cur.get("screen") and self._on_navigate:   # step hands off to a full screen
            self._stop_board_poll()
            self._on_navigate(cur["screen"])
            return
        self._i += 1
        if self._i >= len(steps):
            self._finish()
        else:
            self._render_step()

    def _back(self):
        self._advance_token = getattr(self, "_advance_token", 0) + 1   # cancel auto-advance
        if self._i == 0:
            self._render_name()             # off the first step -> the name step
        else:
            self._i -= 1
            self._render_step()

    def _finish(self):
        path = self._path
        self._stop_current()
        if self._on_complete:
            self._on_complete(path, self._node_name)

    def _stop_current(self):
        self._stop_board_poll()
        # Dismiss the on-screen keyboard on EVERY step change. It only auto-hides
        # on the ENTER key, so advancing with the Next button carried it into the
        # next step — where it sat covering that step's nav buttons (the
        # "step 3 of 4 looks stalled" report, 2026-07-30: Next was simply hidden
        # behind the lingering keyboard).
        try:
            from kivy.app import App
            kb = getattr(App.get_running_app(), "keyboard", None)
            if kb is not None:
                kb.hide()
        except Exception:
            pass
        if self._current is not None and hasattr(self._current, "stop"):
            self._current.stop()
        self._current = None

    # -- board-presence gate ------------------------------------------------
    def _start_board_poll(self, anim, on_present=None):
        """Poll for a work board on the medic's USB; fire *on_present(anim)* the
        moment one appears (default = the guided-flow handler). Checks off-thread
        (serial enumeration)."""
        from kivy.clock import Clock
        self._stop_board_poll()
        handler = on_present or self._on_board_present

        def tick(_dt):
            import threading

            def work():
                present = False
                try:
                    from ui.hw_factories import hardware_present
                    present = hardware_present()
                except Exception:
                    present = False
                if present:
                    Clock.schedule_once(lambda _d: handler(anim), 0)
            threading.Thread(target=work, daemon=True).start()

        self._board_poll = Clock.schedule_interval(tick, 1.2)
        tick(0)                                       # check immediately too

    def _on_board_present(self, anim):
        self._stop_board_poll()
        if hasattr(anim, "mark_connected"):
            anim.mark_connected()
        # A board is here — the green burst fires and Next un-grays (so the operator
        # can go on, and the flow also auto-advances after the celebration below).
        if self._current is not None and hasattr(self._current, "set_next_enabled"):
            self._current.set_next_enabled(True)
        # Let the "Connected!" celebration play, then carry the flow forward on its
        # own — detection drives the wizard, no tap needed. A manual Next/Back
        # bumps the token and cancels this pending auto-advance.
        from kivy.clock import Clock
        self._advance_token = getattr(self, "_advance_token", 0) + 1
        tok = self._advance_token
        Clock.schedule_once(
            lambda _d: (getattr(self, "_advance_token", None) == tok
                        and self._current is not None and self._next()), 2.0)

    def _stop_board_poll(self):
        ev = getattr(self, "_board_poll", None)
        if ev is not None:
            ev.cancel()
            self._board_poll = None
