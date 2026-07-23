"""Birth screen — provision a brand-new node, type selected first.

Three node types, in order: RTNode-2400, RNode (flash any supported board), and
Pi + RNode. RTNode-2400 and Pi + RNode run an injected build workflow on a
background thread with live step progress; RNode first shows the board picker
(all official boards + the custom Wireless Tracker) and then the per-board flash
instructions. Type-B builds end on a photographable birth-certificate card.

Workflow factories are injected, so the heavy lifting stays in the tested core
and this screen is transport-agnostic.
"""

from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from node_profile import RadioConfig
from ui import theme
from ui.onscreen_keyboard import bind_field
from ui.birth import birth_node_types, rnode_board_choices
from ui.board_detect import detect_board
from workflows.rtnode_build import RTNODE_TARGETS, DEFAULT_TARGET

#: Firmware the operator can birth. RTNode-2400 = Grey Hat's standalone transport
#: node (health beacon + remote repair); RNode = a radio for a host; Pi + RNode =
#: both. Auto-detect suggests one; the operator can override.
FIRMWARE_CHOICES = [
    ("rtnode2400", "RTNode-2400  (standalone — reports health, remote-repairable)"),
    ("rnode", "RNode  (radio for a host)"),
    ("pi_rnode", "Pi + RNode  (provision a Pi and its radio)"),
]
FIRMWARE_LABEL = dict(FIRMWARE_CHOICES)
from ui.qr import birth_cert_payload, qr_matrix
from workflows.power_compat import check as power_check

#: Host Pi options for the right-hand dropdown: (power_compat key, display). The
#: last entry is "no Pi" — flash a standalone radio with no host to power it.
PI_HOSTS = [
    ("pi_5_full", "Raspberry Pi 5  (5 A / full USB)"),
    ("pi_5", "Raspberry Pi 5  (3 A supply)"),
    ("pi_4b", "Raspberry Pi 4 B"),
    ("pi_3b_plus", "Raspberry Pi 3 B+"),
    ("pi_3a_plus", "Raspberry Pi 3 A+"),
    ("pi_zero_2w", "Raspberry Pi Zero 2 W"),
    ("none", "None — standalone radio (flash only)"),
]

#: Mitosis (cloning this Node Medic) is restricted to the Heltec Wireless Tracker
#: ONLY at this stage — it's the board whose GPS/location path we've proven on
#: hardware (Jonesey), so a clone's location function is guaranteed correct.
#: Other GPS-capable boards can be added once their GPS is verified end-to-end.
MITOSIS_BOARDS = {"heltec_wireless_tracker"}


def _line(text, color="text_primary", bold=False, size="15sp"):
    # height follows the wrapped text — fixed heights made long lines overlap
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=size, color=theme.hex_to_rgba(theme.COLORS[color]),
                size_hint_y=None)
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    lbl.bind(texture_size=lambda i, ts: setattr(i, "height",
                                                max(dp(26), ts[1] + dp(6))))
    return lbl


class QRCodeWidget(Widget):
    """Draws a QR module matrix (rows of booleans, True = dark) as black
    squares on a white field, including the mandatory light quiet-zone border
    so a phone camera can lock on. Fixed size = (modules + 2*quiet) * scale."""

    def __init__(self, matrix, scale=dp(4), quiet=4, **kwargs):
        super().__init__(**kwargs)
        self._matrix = matrix
        self._scale = scale
        self._quiet = quiet
        span = (len(matrix) + 2 * quiet) * scale
        self.size_hint = (None, None)
        self.size = (span, span)
        self.bind(pos=lambda *a: self._draw(), size=lambda *a: self._draw())
        self._draw()

    def _draw(self):
        self.canvas.clear()
        n = len(self._matrix)
        s, q = self._scale, self._quiet
        span = (n + 2 * q) * s
        x0, y0 = self.pos
        with self.canvas:
            Color(1, 1, 1, 1)                      # white field + quiet zone
            Rectangle(pos=(x0, y0), size=(span, span))
            Color(0, 0, 0, 1)                      # dark modules
            for r, row in enumerate(self._matrix):
                # matrix row 0 is the TOP; Kivy y grows upward, so flip rows
                yy = y0 + (q + (n - 1 - r)) * s
                for c, dark in enumerate(row):
                    if dark:
                        Rectangle(pos=(x0 + (q + c) * s, yy), size=(s, s))


class BirthScreen(BoxLayout):
    def __init__(self, workflow_factories, rnode_flash_factory=None,
                 on_mitosis=None, prefill_location=None, on_use_existing=None,
                 node_source=None, on_guide=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        # (lat, lon, source) stamped from the map's "Use this position", or None.
        self._prefill_location = prefill_location
        # on_use_existing(cert) — search-existing picked a birthed node (-> Triage).
        self._on_use_existing = on_use_existing
        # on_guide() — open the step-by-step guided birth (for a new operator).
        self._on_guide = on_guide
        # When arriving from the guide with a chosen kind, don't let auto-detect
        # flip the firmware family out from under the operator (cleared on a manual
        # firmware tap). None = detection decides (the 'radio' path).
        self._forced_firmware = None
        # node_source(query) -> [node dicts] for nodes the medic KNOWS on the mesh
        # (kin roster + discovered), so search finds e.g. FAITH even if it wasn't
        # birthed through this medic's cert store. Injected; None in tests.
        self._node_source = node_source
        self._saved_cert_id = None
        # Step one is naming the node (build a NEW one) OR searching for one already
        # birthed. Created once and re-parented on each header rebuild so a typed
        # name survives board changes. Notes are asked at the END (after the cert).
        self._name_in = TextInput(hint_text="Name this node  (e.g. Rooftop-East)",
                                  multiline=False, size_hint_y=None, height=dp(46),
                                  font_size="16sp")
        self._search_in = TextInput(hint_text="Search a node you already birthed…",
                                    multiline=False, size_hint_y=None, height=dp(46),
                                    font_size="15sp")
        self._search_in.bind(text=lambda i, v: self._run_search(v))
        self._end_notes_in = TextInput(
            hint_text="Notes  (optional — mast height, landmarks…)",
            multiline=True, size_hint_y=None, height=dp(70), font_size="15sp")
        bind_field(self._name_in)
        bind_field(self._search_in)
        bind_field(self._end_notes_in)
        # {"rtnode2400": factory, "pi_rnode": factory} — each returns a workflow
        # with .run_all(on_progress), .birth_certificate, and (optionally)
        # .onboarding. The "rnode" type has no single workflow: it opens the
        # board picker first.
        self._factories = workflow_factories
        self._on_mitosis = on_mitosis
        # rnode_flash_factory(board) -> an RNodeFlashWorkflow for that board.
        self._rnode_flash_factory = rnode_flash_factory
        self._workflow = None

        self._labels = dict(birth_node_types())      # key -> display label
        self._boards = list(rnode_board_choices())
        self._sel_board = None                       # chosen RNodeBoard | None
        self._sel_pi = None                          # chosen (key, name) | None
        self._firmware = None                        # rtnode2400 | rnode | pi_rnode
        self._rtnode_target = None                   # RTNODE_TARGETS key (RTNode-2400)
        self._detected = None                        # last board_detect result
        self._detecting = False

        self.header = BoxLayout(orientation="vertical", size_hint_y=None,
                                spacing=dp(6))
        self.header.bind(minimum_height=self.header.setter("height"))
        self.add_widget(self.header)
        self._build_chooser()

        self.scroll = ScrollView()
        self.list = BoxLayout(orientation="vertical", size_hint_y=None,
                              spacing=dp(2))
        self.list.bind(minimum_height=self.list.setter("height"))
        self.scroll.add_widget(self.list)
        self.add_widget(self.scroll)

    def _sel_button(self, label, on_tap):
        """A wide tappable selector showing the current pick (or a prompt)."""
        btn = Button(text=label, size_hint_y=None, height=dp(52), halign="left",
                     font_size="16sp", background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        btn.bind(size=lambda i, v: setattr(i, "text_size",
                                           (v[0] - dp(20), v[1])))
        btn.bind(on_release=lambda *_: on_tap())
        return btn

    def _build_chooser(self):
        """The hardware chooser: a Board picker (required) and a Host Pi picker
        (optional — board-only is a standalone radio). Each opens a numbered,
        scrollable list; a Spinner dropdown flickered shut on this panel."""
        self.header.clear_widgets()
        if hasattr(self, "list"):
            self.list.clear_widgets()
        self.header.add_widget(_line("Birth a new node", bold=True, size="22sp"))

        # New here? A step-by-step guide with animations walks the whole thing.
        if self._on_guide is not None:
            guide = Button(text="New here?  Guide me step by step  →",
                           size_hint_y=None, height=dp(52), bold=True, font_size="16sp",
                           background_normal="",
                           background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                           color=theme.hex_to_rgba(theme.COLORS["background"]))
            guide.bind(on_release=lambda *_: self._on_guide())
            self.header.add_widget(guide)
            self.header.add_widget(Widget(size_hint_y=None, height=dp(8)))

        # Step one: name a NEW node (build it), or search one already birthed.
        self.header.add_widget(_line("Name this node", bold=True, size="15sp",
                                     color="accent"))
        self.header.add_widget(self._name_in)
        if self._prefill_location:
            lat, lon, src = self._prefill_location
            self.header.add_widget(_line(
                f"Location stamped: {lat:.5f}, {lon:.5f}  (from {src})",
                size="12.5sp", color="green"))

        self.header.add_widget(Widget(size_hint_y=None, height=dp(8)))
        self.header.add_widget(_line("— or — use existing node", bold=True,
                                     size="15sp", color="accent"))
        self.header.add_widget(self._search_in)
        self._search_results = BoxLayout(orientation="vertical", size_hint_y=None,
                                         height=dp(0), spacing=dp(2))
        self._search_results.bind(minimum_height=self._search_results.setter("height"))
        self.header.add_widget(self._search_results)

        self.header.add_widget(Widget(size_hint_y=None, height=dp(12)))
        self.header.add_widget(_line("Choose your hardware:", size="13sp",
                                     color="text_secondary"))

        # Auto-detect: read the plugged-in board's chip and pre-select firmware.
        detect = Button(
            text="Detecting board…" if self._detecting else "Detect connected board",
            size_hint_y=None, height=dp(48), bold=True, disabled=self._detecting,
            background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
            color=theme.hex_to_rgba(theme.COLORS["background"]))
        detect.bind(on_release=lambda *_: self._detect_board())
        self.header.add_widget(detect)
        if self._detected is not None:
            found = self._detected.get("found")
            self.header.add_widget(_line(self._detect_summary(), size="12.5sp",
                                         color="green" if found else "amber"))

        # Firmware — auto-detect suggests one, but ALL options are shown as buttons
        # so the operator can pick RNode / Pi+RNode directly (the selected one is
        # highlighted).
        self.header.add_widget(_line("Firmware", bold=True, size="15sp",
                                     color="accent"))
        fw_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(50),
                           spacing=dp(6))
        for key, short in (("rtnode2400", "RTNode-2400"), ("rnode", "RNode"),
                           ("pi_rnode", "Pi + RNode")):
            sel = self._firmware == key
            b = Button(text=short, font_size="14sp", bold=True, background_normal="",
                       background_color=theme.hex_to_rgba(
                           theme.COLORS["accent" if sel else "surface"]),
                       color=theme.hex_to_rgba(
                           theme.COLORS["background" if sel else "text_primary"]))
            b.bind(on_release=lambda _b, k=key: self._pick_firmware(k))
            fw_row.add_widget(b)
        self.header.add_widget(fw_row)
        if self._firmware:
            self.header.add_widget(_line(FIRMWARE_LABEL[self._firmware], size="12sp",
                                         color="text_secondary"))

        if self._firmware == "rtnode2400":
            self.header.add_widget(_line("Target board", bold=True, size="15sp",
                                         color="accent"))
            tgt = RTNODE_TARGETS.get(self._rtnode_target)
            self.header.add_widget(self._sel_button(
                tgt.display if tgt else "Tap to choose the RTNode-2400 target",
                self._choose_rtnode_target))
        elif self._firmware in ("rnode", "pi_rnode"):
            self.header.add_widget(_line("Board (radio)", bold=True, size="15sp",
                                         color="accent"))
            self.header.add_widget(self._sel_button(
                self._sel_board.display_name if self._sel_board
                else "Tap to choose a board", self._choose_board))
            if self._firmware == "pi_rnode":
                self.header.add_widget(Widget(size_hint_y=None, height=dp(10)))
                self.header.add_widget(_line("Host Pi", bold=True, size="15sp",
                                             color="accent"))
                self.header.add_widget(self._sel_button(
                    self._sel_pi[1] if self._sel_pi else "Tap to choose a Pi",
                    self._choose_pi))

        # Mitosis (clone THIS Node Medic) — Heltec Wireless Tracker only (proven
        # GPS path); available from the RNode board path.
        mit_ok = self._sel_board is not None and self._sel_board.key in MITOSIS_BOARDS
        self.header.add_widget(Widget(size_hint_y=None, height=dp(14)))
        mit = Button(text="Mitosis — clone this Node Medic",
                     size_hint_y=None, height=dp(50), font_size="15sp",
                     disabled=not mit_ok, background_normal="",
                     background_color=theme.hex_to_rgba(
                         theme.COLORS["green" if mit_ok else "surface"]),
                     color=theme.hex_to_rgba(
                         theme.COLORS["background" if mit_ok else "text_secondary"]))
        mit.bind(on_release=lambda *_: self._on_mitosis and self._on_mitosis())
        self.header.add_widget(mit)

        # The scroll below shows the next action for the chosen firmware.
        if hasattr(self, "list"):
            self._build_action()

    def _build_action(self):
        """Populate the scroll with the next step for the chosen firmware: the
        RTNode-2400 build button, the RNode radio-params form, or a prompt."""
        self.list.clear_widgets()
        if self._firmware == "rtnode2400":
            if self._rtnode_target:
                tgt = RTNODE_TARGETS[self._rtnode_target]
                b = Button(text=f"Build RTNode-2400 ({tgt.display})",
                           size_hint_y=None, height=dp(56), bold=True, font_size="17sp",
                           background_normal="",
                           background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                           color=theme.hex_to_rgba(theme.COLORS["background"]))
                b.bind(on_release=lambda *_: self._run_rtnode())
                self.list.add_widget(b)
                self.list.add_widget(_line(
                    "Flashes the attached board with RTNode-2400 and provisions it "
                    "on the standard channel. WiFi/LoRa details are entered on the "
                    "node's setup portal after flashing.", size="12.5sp",
                    color="text_secondary"))
            else:
                self.list.add_widget(_line("Choose the RTNode-2400 target above.",
                                           size="13sp", color="text_secondary"))
        elif self._firmware in ("rnode", "pi_rnode"):
            if self._sel_board is not None:
                self.show_params(self._firmware, board=self._sel_board)
            else:
                self.list.add_widget(_line("Pick a board above to set radio params "
                                           "and start.", size="13sp",
                                           color="text_secondary"))
        else:
            self.list.add_widget(_line(
                "Detect the connected board, or choose firmware, to begin.",
                size="13sp", color="text_secondary"))

    def _option_button(self, num, text, on_tap):
        btn = Button(text=f"{num:>2}.  {text}", size_hint_y=None, height=dp(46),
                     halign="left", font_size="15sp", background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        btn.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(20), v[1])))
        btn.bind(on_release=lambda *_: on_tap())
        return btn

    def _picker_popup(self, title, entries):
        """A full-screen scrollable picker. The board/Pi lists are long (15 boards),
        so a modal gives them the whole screen instead of a cramped strip crushed
        under the header (where only 3-4 showed and couldn't be scrolled to)."""
        from kivy.uix.popup import Popup
        root = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(6))
        scroll = ScrollView()
        lst = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        lst.bind(minimum_height=lst.setter("height"))
        popup = Popup(title=title, size_hint=(0.96, 0.94), title_size="17sp",
                      separator_color=theme.hex_to_rgba(theme.COLORS["accent"]))
        for num, text, cb in entries:
            lst.add_widget(self._option_button(
                num, text, lambda cb=cb: (popup.dismiss(), cb())))
        scroll.add_widget(lst)
        root.add_widget(scroll)
        cancel = Button(text="Cancel", size_hint_y=None, height=dp(48), bold=True,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        cancel.bind(on_release=lambda *_: popup.dismiss())
        root.add_widget(cancel)
        popup.content = root
        popup.open()

    def _choose_board(self):
        """Full-screen picker of every flashable board. Numbers match rnodeconf's
        own autoinstall menu (Heltec V4 = 9, …) so the screen and Mark Qvist's
        terminal flow never disagree; custom boards continue after."""
        official = [b for b in self._boards if b.flash_method == "autoinstall"]
        next_custom = max((b.autoinstall_index for b in official), default=0) + 1
        entries = []
        for board in self._boards:
            if board.flash_method == "autoinstall":
                num, tag = board.autoinstall_index, ""
            else:
                num, tag = next_custom, "  (custom)"
                next_custom += 1
            entries.append((num, f"{board.display_name}  [{board.platform}]{tag}",
                            lambda b=board: self._pick_board(b)))
        self._picker_popup("Select the board", entries)

    def _pick_board(self, board):
        self._sel_board = board
        self._build_chooser()

    def _choose_pi(self):
        """Full-screen picker of host Pis — plus 'None' for a standalone radio."""
        entries = [(i, name, lambda k=key, n=name: self._pick_pi(k, n))
                   for i, (key, name) in enumerate(PI_HOSTS, 1)]
        self._picker_popup("Select the host Pi", entries)

    def _pick_pi(self, key, name):
        self._sel_pi = (key, name)
        self._build_chooser()

    # -- auto-detect + firmware ---------------------------------------------

    def _detect_board(self):
        """Read the plugged-in board's chip (off-thread) and pre-select firmware
        (and board/target when unambiguous)."""
        if self._detecting:
            return
        self._detecting = True
        self._build_chooser()                         # show "Detecting board…"
        import threading

        def work():
            res = detect_board(self._boards)
            Clock.schedule_once(lambda dt: self._detected_done(res), 0)
        threading.Thread(target=work, daemon=True).start()

    def _detected_done(self, res):
        self._detecting = False
        self._detected = res
        if res.get("found"):
            if not self._forced_firmware:                 # guide-chosen kind wins
                self._firmware = (res.get("firmware") or ["rnode"])[0]
            if self._firmware == "rtnode2400":
                # a chip read can't tell the S3 boards apart — default the target
                self._rtnode_target = self._rtnode_target or DEFAULT_TARGET
            elif res.get("board_key"):
                self._sel_board = next(
                    (b for b in self._boards if b.key == res["board_key"]), None)
        self._build_chooser()

    def _detect_summary(self):
        d = self._detected or {}
        if not d.get("found"):
            return d.get("reason", "No board detected.")
        fw = (d.get("firmware") or ["rnode"])[0]
        fw_short = FIRMWARE_LABEL.get(fw, fw).split("  ")[0]
        return (f"Detected {d.get('platform', d.get('chip'))} on {d.get('port')}"
                f"  -  suggests {fw_short}")

    def _choose_firmware(self):
        entries = [(i, label, lambda k=key: self._pick_firmware(k))
                   for i, (key, label) in enumerate(FIRMWARE_CHOICES, 1)]
        self._picker_popup("Choose firmware", entries)

    def _pick_firmware(self, key):
        self._firmware = key
        self._forced_firmware = None            # a manual tap is an explicit override
        if key == "rtnode2400" and not self._rtnode_target:
            self._rtnode_target = DEFAULT_TARGET
        self._build_chooser()

    def begin_guided(self, path):
        """Arrived from the step-by-step guide. Pre-scope the firmware for the chosen
        kind (radio = let detection decide; host = RNode; pi = Pi + RNode) and
        auto-run detection, since the board is already plugged in per the guide — so
        the operator lands on naming + a suggested setup, not a cold form."""
        self._forced_firmware = {"radio": "rtnode2400", "host": "rnode",
                                 "pi": "pi_rnode"}.get(path)
        if self._forced_firmware:
            self._firmware = self._forced_firmware
            if self._forced_firmware == "rtnode2400" and not self._rtnode_target:
                self._rtnode_target = DEFAULT_TARGET
        self._build_chooser()
        self._detect_board()

    def _choose_rtnode_target(self):
        entries = [(i, t.display, lambda k=key: self._pick_rtnode_target(k))
                   for i, (key, t) in enumerate(RTNODE_TARGETS.items(), 1)]
        self._picker_popup("RTNode-2400 target", entries)

    def _pick_rtnode_target(self, key):
        self._rtnode_target = key
        self._build_chooser()

    def _run_rtnode(self):
        """Kick off the real RTNode-2400 build on the attached board (or honest-fail
        with why, if it can't run)."""
        workflow = self._factories["rtnode2400"](self._rtnode_target)
        tgt = RTNODE_TARGETS[self._rtnode_target]
        if getattr(workflow, "is_blocked", False):
            from ui.requirement_popup import requirement_popup
            requirement_popup(workflow.message,
                              getattr(workflow, "title", "Heads up"),
                              getattr(workflow, "under_construction", False))
            return
        self._launch(workflow, f"Building RTNode-2400 ({tgt.display})…")

    def _show_power_popup(self, verdict, board_name, pi_key, on_proceed):
        """Warn that this Pi can't power this board over USB — Proceed (⚠ red,
        bottom-left) / Cancel (green, bottom-right)."""
        pi_name = next((n for k, n in PI_HOSTS if k == pi_key), pi_key)
        body = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(6))
        headline = ("blocked" if verdict["verdict"] == "blocked"
                    else "may brown out")
        body.add_widget(_line(
            f"The {pi_name} may not power the {board_name} over USB — {headline}.",
            bold=True, size="16sp", color="amber"))
        body.add_widget(_line(verdict.get("why", ""), size="14sp"))
        body.add_widget(_line(
            f"Use a POWERED USB HUB between the Pi and the {board_name}, or it "
            "can fail mid-flash / mid-transmit.", size="14sp", color="amber"))
        for rem in verdict.get("remedies", [])[:3]:
            body.add_widget(_line("  • " + rem, size="12sp",
                                  color="text_secondary"))
        btns = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=dp(56), spacing=dp(10))
        proceed = Button(text="⚠  Proceed anyway", font_size="16sp",
                         bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel = Button(text="Cancel", font_size="16sp", bold=True,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
        btns.add_widget(proceed)       # bottom-left
        btns.add_widget(cancel)        # bottom-right
        body.add_widget(btns)
        popup = Popup(title="Power warning", content=body, size_hint=(0.92, 0.72),
                      title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                      separator_color=theme.hex_to_rgba(theme.COLORS["red"]))
        proceed.bind(on_release=lambda *_: (popup.dismiss(), on_proceed()))
        cancel.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    # -- radio-params form (pre-filled with our canonical settings) ----------

    def show_params(self, node_type, board=None):
        """A short form pre-filled with the canonical radio config. The user just
        taps OK — or edits a field first. One big OK confirms and starts."""
        self.list.clear_widgets()
        self._param_inputs = {}
        from provisioning.radio_defaults import load_defaults
        dd = load_defaults()                              # tool-wide defaults (Settings)
        if board is not None:
            self.list.add_widget(_line(f"{board.display_name}", bold=True,
                                       size="16sp"))
        self.list.add_widget(_line(
            "Radio settings — pre-filled with your tool defaults. Change only "
            "if you know why, then press OK.", size="14sp"))
        fields = [
            ("freq", "Frequency (MHz)", f"{dd['freq']:g}"),
            ("bw", "Bandwidth (kHz)", f"{dd['bw']:g}"),
            ("sf", "Spreading factor", str(dd['sf'])),
            ("cr", "Coding rate", str(dd['cr'])),
            ("txp", "TX power (dBm)", str(dd['txp'])),
        ]
        for key, label, value in fields:
            self.list.add_widget(self._param_row(key, label, value))
        ok = Button(text="OK — start", size_hint_y=None, height=dp(60),
                    font_size="20sp", bold=True, background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        ok.bind(on_release=lambda *_a: self._confirm_params(node_type, board))
        self.list.add_widget(ok)

    def _param_row(self, key, label, value):
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(50),
                        spacing=dp(8))
        row.add_widget(_line(label, size="15sp"))
        ti = TextInput(text=value, multiline=False, size_hint=(None, None),
                       width=dp(160), height=dp(44), font_size="18sp",
                       input_filter="float" if key in ("freq", "bw") else "int")
        bind_field(ti, numeric=True)                 # number pad for radio params
        self._param_inputs[key] = ti
        row.add_widget(ti)
        return row

    def _read_params(self):
        from provisioning.radio_defaults import load_defaults
        dd = load_defaults()

        def num(key, cast, default):
            try:
                return cast(self._param_inputs[key].text.strip())
            except (ValueError, KeyError):
                return default            # blank/garbage falls back to the default

        return {
            "freq": num("freq", float, dd["freq"]),
            "bw": num("bw", float, dd["bw"]),
            "sf": num("sf", int, dd["sf"]),
            "cr": num("cr", int, dd["cr"]),
            "txp": num("txp", int, dd["txp"]),
        }

    def _apply_radio(self, workflow, radio):
        cfg = RadioConfig(frequency_mhz=radio["freq"], bandwidth_khz=radio["bw"],
                          spreading_factor=radio["sf"], coding_rate=radio["cr"],
                          tx_power_dbm=radio["txp"])
        # Pi builds carry a profile.radio; standalone RNode flashes (incl. the V4
        # RGB workflow) carry a plain .radio. Set whichever the workflow exposes
        # so the form values are actually baked at birth (not silently dropped).
        r = getattr(getattr(workflow, "profile", None), "radio", None)
        if r is not None:
            r.frequency_mhz = cfg.frequency_mhz
            r.bandwidth_khz = cfg.bandwidth_khz
            r.spreading_factor = cfg.spreading_factor
            r.coding_rate = cfg.coding_rate
            r.tx_power_dbm = cfg.tx_power_dbm
        if hasattr(workflow, "radio"):
            workflow.radio = cfg

    def _confirm_params(self, node_type, board):
        """OK — start. If the path can't run (not built yet / no board), say so up
        front — a single popup, no pointless power warning for something that
        won't run. Otherwise warn on a brownout-prone Pi+board combo, then build."""
        workflow, title = self._make_workflow(node_type, board)
        if getattr(workflow, "is_blocked", False):
            from ui.requirement_popup import requirement_popup
            requirement_popup(workflow.message, getattr(workflow, "title", "Heads up"),
                              getattr(workflow, "under_construction", False))
            return
        pi_key = self._sel_pi[0] if self._sel_pi else "none"
        if pi_key != "none" and board is not None:
            verdict = power_check(pi_key, board.key)
            if verdict and verdict.get("verdict") in ("blocked", "caution"):
                self._show_power_popup(
                    verdict, board.display_name, pi_key,
                    lambda: self._launch(workflow, title))
                return
        self._launch(workflow, title)

    def _make_workflow(self, node_type, board):
        """Create the workflow (+ progress title) for this selection and bake the
        radio params in. Returns ``(workflow, title)``."""
        radio = self._read_params()
        self._last_board = board                 # remembered for the outcome panel
        self._last_type = node_type
        if node_type == "pi_rnode":
            # Pi + RNode: provision the Pi AND flash the chosen board it hosts.
            workflow = self._factories["pi_rnode"]()
            prof = getattr(workflow, "profile", None)
            if prof is not None and board is not None:
                prof.rnode_board_key = board.key
            title = (f"Building Pi + {board.display_name}..." if board
                     else "Building Pi + RNode...")
        elif board is not None:                  # standalone RNode flash (no Pi)
            workflow = self._rnode_flash_factory(board)
            title = f"Flashing {board.display_name}..."
        else:                                    # RTNode-2400
            workflow = self._factories[node_type]()
            title = f"Building {self._labels.get(node_type, node_type)}..."
        self._apply_radio(workflow, radio)
        return workflow, title

    def _launch(self, workflow, title):
        # Blocked path (no board attached / not wired to real hardware yet): say so
        # in a plain popup instead of faking a run or dumping a failed-step log.
        if getattr(workflow, "is_blocked", False):
            from ui.requirement_popup import requirement_popup
            requirement_popup(workflow.message,
                              getattr(workflow, "title", "Heads up"),
                              getattr(workflow, "under_construction", False))
            return
        self.list.clear_widgets()
        self.list.add_widget(_line(title, bold=True))
        self._workflow = workflow
        self._had_failure = False                # reset for this run's outcome
        threading.Thread(target=self._run, daemon=True).start()

    def show_boards(self):
        """List every board the tool can flash as an RNode (official first,
        the custom Wireless Tracker last)."""
        self.list.clear_widgets()
        self.list.add_widget(_line("Select the board to flash as an RNode:",
                                   bold=True, size="16sp"))
        official = [b for b in rnode_board_choices()
                    if b.flash_method == "autoinstall"]
        next_custom = max(b.autoinstall_index for b in official) + 1
        for board in rnode_board_choices():
            # numbers match rnodeconf's own autoinstall menu, so the screen
            # and Mark Qvist's terminal flow never disagree; custom boards
            # continue the numbering after the official list
            if board.flash_method == "autoinstall":
                num = board.autoinstall_index
                tag = ""
            else:
                num = next_custom
                next_custom += 1
                tag = "  (custom)"
            btn = Button(
                text=f"{num:>2}.  {board.display_name}  [{board.platform}]{tag}",
                size_hint_y=None, height=dp(40), halign="left",
                background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            btn.bind(on_release=lambda *_a, b=board: self.show_board_detail(b))
            self.list.add_widget(btn)

    def show_board_detail(self, board):
        """Per-board flash guidance: how it's flashed, buttons, and recovery."""
        self.list.clear_widgets()
        self.list.add_widget(_line(board.display_name, bold=True, size="17sp"))
        self.list.add_widget(_line(f"Platform: {board.platform}   Modem: "
                                   f"{board.modem}   Bands: {board.bands}",
                                   size="13sp"))
        if board.flash_method == "autoinstall":
            how = ("Flashed from the offline firmware cache via "
                   "rnodeconf --autoinstall (device menu option "
                   f"{board.autoinstall_index}).")
        else:
            how = ("Custom board — built from patched RNode_Firmware with "
                   "arduino-cli.")
        self.list.add_widget(_line(how, size="13sp"))
        self.list.add_widget(_line("Enter bootloader:", bold=True, size="14sp"))
        self.list.add_widget(_line(board.bootloader_instructions, size="12sp"))
        self.list.add_widget(_line("If interrupted:", bold=True, size="14sp"))
        self.list.add_widget(_line(board.recovery_instructions, size="12sp"))
        if board.notes:
            self.list.add_widget(_line(board.notes, color="amber", size="12sp"))
        # Flash action — only for autoinstall boards with a verified sequence and
        # an injected flash factory (the tool flashes the locally attached board).
        if (board.flash_method == "autoinstall" and board.autoinstall_bands
                and self._rnode_flash_factory is not None):
            flash_btn = Button(
                text=f"Flash this board as an RNode", size_hint_y=None,
                height=dp(48), background_normal="",
                background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                color=theme.hex_to_rgba(theme.COLORS["background"]))
            flash_btn.bind(on_release=lambda *_a, b=board:
                           self.show_params("rnode", board=b))
            self.list.add_widget(flash_btn)

    def _run(self):
        self._workflow.run_all(on_progress=lambda r:
                              Clock.schedule_once(lambda dt: self._step(r), 0))
        Clock.schedule_once(lambda dt: self._finish(), 0)

    def _step(self, result):
        mark = "skip" if result.skipped else ("ok" if result.success else "FAIL")
        color = ("text_secondary" if result.skipped
                 else "green" if result.success else "red")
        if not result.success and not result.skipped:
            self._had_failure = True
        self.list.add_widget(_line(f"  [{mark}] {result.name}", color=color,
                                   size="14sp"))
        # Surface the reason on failure — otherwise an honest "not wired yet /
        # plug the board in" message is swallowed and only the step name shows.
        if not result.success and not result.skipped and getattr(result, "message", ""):
            self.list.add_widget(_line(f"      {result.message}", color="amber",
                                       size="12sp"))

    def _outcome_panel(self):
        """A clear '✓ Done — next steps' (or failure) banner so the operator is
        never left staring at a finished log wondering what to do."""
        board = getattr(self, "_last_board", None)
        if getattr(self, "_had_failure", False):
            self.list.add_widget(_line("X  Something didn't finish", bold=True,
                                       size="18sp", color="red"))
            self.list.add_widget(_line(
                "Fix the failed step above and run it again. If a board won't "
                "flash: hold BOOT, tap RST, release BOOT, then retry - or use a "
                "short, known-good USB data cable.", size="14sp", color="amber"))
            return
        self.list.add_widget(_line("OK  Done!", bold=True, size="20sp",
                                   color="green"))
        if board is not None:
            nxt = (f"{board.display_name} is flashed & verified as an RNode on the "
                   "standard channel (915.125 / 125 / SF9 / CR5 / 17 dBm). "
                   "Unplug it and fit it to its node/Pi - it's ready to run.")
        else:
            nxt = ("Node provisioned on the standard channel. Give it power and "
                   "its antenna; it will announce and appear in VITALS as kin.")
        self.list.add_widget(_line(nxt, size="15sp"))
        self.list.add_widget(_line("Birth another with Change at the top, or hit "
                                   "BACK.", size="13sp", color="text_secondary"))

    def _finish(self):
        self._outcome_panel()
        onboarding = getattr(self._workflow, "onboarding", None)
        if onboarding:
            self.list.add_widget(_line("Onboarding (enter at RTNode-Setup / "
                                       "http://10.0.0.1):", bold=True,
                                       size="16sp"))
            for k in ("node_name", "ssid", "psk", "freq", "bw", "sf", "cr",
                      "txp", "advert_en", "advert_lat", "advert_lon",
                      "advert_jitter"):
                if k not in onboarding:
                    continue
                v = onboarding.get(k, "")
                shown = v if v != "" else "____  (operator)"
                self.list.add_widget(_line(f"    {k}: {shown}", size="13sp"))

        cert = getattr(self._workflow, "birth_certificate", None)
        if cert:
            cert = self._stamp_identity(dict(cert))   # name + location
            # A completed BIRTH consumes any active triage survey, which auto-clears
            # it so it can't carry over to the next build.
            try:
                from monitor import triage
                triage.consume_active_session()
            except Exception:
                pass
            from ui.cert_store import save_cert
            try:
                self._saved_cert_id = save_cert(cert)     # keep it on the medic
                cert["_id"] = self._saved_cert_id
            except OSError:
                self._saved_cert_id = None
            self._cert = cert
            self.list.add_widget(_line("Birth certificate:", bold=True,
                                       size="16sp"))
            self.list.add_widget(_line("    (saved on this Node Medic)",
                                       size="12sp", color="text_secondary"))
            for k, v in cert.items():
                if k.startswith("_"):
                    continue
                self.list.add_widget(_line(f"    {k}: {v}", size="13sp"))
            self._add_cert_qr(cert)
            self._add_notes_panel()

    def _add_notes_panel(self):
        """Notes are asked HERE — after the certificate is out — then saved onto
        the stored cert (and regenerate the QR so a scan carries them too)."""
        self.list.add_widget(Widget(size_hint_y=None, height=dp(8)))
        self.list.add_widget(_line("Add notes", bold=True, size="16sp",
                                   color="accent"))
        self._end_notes_in.text = getattr(self, "_cert", {}).get("notes", "")
        self.list.add_widget(self._end_notes_in)
        save = Button(text="Save notes to certificate", size_hint_y=None,
                      height=dp(48), bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        save.bind(on_release=lambda *_: self._save_notes())
        self.list.add_widget(save)
        self._notes_status = _line("", size="12.5sp", color="green")
        self.list.add_widget(self._notes_status)

    def _save_notes(self):
        notes = self._end_notes_in.text.strip()
        cert = getattr(self, "_cert", None)
        if cert is None:
            return
        cert["notes"] = notes
        if self._saved_cert_id:
            from ui.cert_store import update_notes
            update_notes(self._saved_cert_id, notes)
        self._notes_status.text = "Saved. (The QR above now includes the notes.)"
        # refresh the QR so a fresh scan carries the notes
        self._add_cert_qr(cert)

    def set_prefill_location(self, lat, lon, source):
        """Stamp a location onto this birth (from the map's 'Use this position').
        Shown as 'Location stamped …' and folded into the certificate at the end."""
        self._prefill_location = (lat, lon, source)
        self._build_chooser()

    def prefill_name(self, name):
        """Seed the 'Name this node' field — used when the operator taps a node the
        medic never birthed and chooses to birth it here (from the cert viewer's
        'not birthed here' nudge)."""
        self._build_chooser()               # ensure the name field exists
        if getattr(self, "_name_in", None) is not None:
            self._name_in.text = str(name or "")

    def _run_search(self, query):
        """Find an already-provisioned node by name — from the on-medic certificate
        store AND from the nodes the medic knows on the mesh (kin roster +
        discovered, via node_source). Picking one hands it to Triage."""
        from ui.cert_store import search_certs
        self._search_results.clear_widgets()
        query = (query or "").strip()
        if not query:
            self._search_results.height = dp(0)
            return
        hits = list(search_certs(query))
        seen = {(c.get("node_name") or "").lower() for c in hits}
        if self._node_source:                     # merge in known mesh nodes
            try:
                for node in self._node_source(query):
                    nm = (node.get("node_name") or "").lower()
                    if nm and nm not in seen:
                        seen.add(nm)
                        hits.append(node)
            except Exception:
                pass
        hits = hits[:6]
        if not hits:
            self._show_rebirth_nudge(query)
            return
        for cert in hits:
            name = cert.get("node_name") or cert.get("hostname") or "(unnamed node)"
            loc = cert.get("location")
            tag = "  · on mesh" if cert.get("_source") == "mesh" and not loc else ""
            label = f"{name}" + (f"   · {loc}" if loc else tag)
            btn = Button(text=label, size_hint_y=None, height=dp(44), halign="left",
                         font_size="14sp", background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            btn.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(16), v[1])))
            btn.bind(on_release=lambda _b, c=cert: self._pick_existing(c))
            self._search_results.add_widget(btn)

    def _show_rebirth_nudge(self, query):
        """No node by that name is known — encourage birthing it THROUGH the medic,
        because that's what makes it report health back here and be repairable
        remotely. Tapping the button carries the name into the birth flow."""
        self._search_results.add_widget(_line(
            f"'{query}' isn't set up with Node Medic yet.", bold=True, size="13.5sp"))
        self._search_results.add_widget(_line(
            "Birth it through Node Medic so it reports its health back here and can "
            "be repaired remotely — then it'll show up here for good.",
            size="12.5sp", color="text_secondary"))
        btn = Button(text=f"Birth '{query}' through Node Medic", size_hint_y=None,
                     height=dp(50), bold=True, font_size="14.5sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                     color=theme.hex_to_rgba(theme.COLORS["background"]))
        btn.bind(size=lambda i, v: setattr(i, "text_size", (v[0] - dp(16), v[1])))
        btn.bind(on_release=lambda *_: self._start_birth_named(query))
        self._search_results.add_widget(btn)

    def _start_birth_named(self, query):
        """Carry the searched name into the 'Name this node' field and clear the
        search, so the operator drops straight into building it (pick RTNode-2400,
        etc.) — the node is (re)born through the medic and becomes manageable."""
        self._name_in.text = query
        self._search_in.text = ""          # clears the search + its results

    def _pick_existing(self, cert):
        """An already-birthed node was chosen — it's provisioned, so hand it to
        Triage (adjust the antenna where it's being mounted)."""
        if self._prefill_location and "location" not in cert:
            lat, lon, src = self._prefill_location
            cert["location"] = f"{lat:.6f}, {lon:.6f} ({src})"  # new mount spot
            if cert.get("_id"):
                from ui.cert_store import save_cert
                save_cert(cert)
        if self._on_use_existing:
            self._on_use_existing(cert)

    def _stamp_identity(self, cert):
        """Fold the operator's node name and the map-stamped location into the
        certificate dict (notes are added at the END, after the cert is shown)."""
        name = self._name_in.text.strip()
        if name:
            cert["node_name"] = name
        if self._prefill_location and "location" not in cert:
            lat, lon, src = self._prefill_location
            cert["location"] = f"{lat:.6f}, {lon:.6f} ({src})"
        return cert

    def _add_cert_qr(self, cert):
        """Show the certificate as a scannable QR — the medic has no phone
        tethered, so this is how the operator gets it off the device: scan with
        any camera, no pairing or network. Falls back to a hint if segno is
        absent (the text above is still the record)."""
        # Drop any QR drawn earlier (e.g. before notes were added) so a refresh
        # replaces it rather than stacking a second code.
        for w in getattr(self, "_qr_widgets", []):
            if w.parent:
                self.list.remove_widget(w)
        self._qr_widgets = []
        matrix = qr_matrix(birth_cert_payload(cert))
        if not matrix:
            w = _line("    (install 'segno' on the medic to show a scannable QR)",
                      color="text_secondary", size="12sp")
            self.list.add_widget(w)
            self._qr_widgets = [w]
            return
        lbl = _line("Scan to save this certificate:", bold=True, size="15sp")
        self.list.add_widget(lbl)
        qr = QRCodeWidget(matrix)
        holder = AnchorLayout(anchor_x="center", size_hint_y=None,
                              height=qr.height + dp(12))
        holder.add_widget(qr)
        self.list.add_widget(holder)
        self._qr_widgets = [lbl, holder]
