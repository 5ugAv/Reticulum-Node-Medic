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

#: Rough seconds per build step, so the progress ring fills by ESTIMATED TIME (the
#: firmware compile dominates) rather than jumping one flat notch per step. Under-
#: estimates just snap forward when a step completes early (a cached rebuild is fast).
_STEP_SECONDS = {
    "detect_board": 8, "detect_port": 5, "detect_hardware": 20, "ensure_single_board": 3,
    "ensure_firmware": 25, "confirm_radio_parameters": 2,
    # flash_firmware: ~45 s for the RGB full-image write, longer for RTNode's
    # pio upload — 90 keeps both bars moving (300 sat at a dot then teleported
    # full; live pacing feedback 2026-07-31).
    "flash_firmware": 90, "flash": 170, "flash_rnode_firmware": 150,
    "set_params": 15, "set_firmware_radio_parameters": 15, "set_params_at_birth": 15,
    "wifi_onboarding": 2, "verify_beacon": 25, "verify_sd_overflow": 3,
    # provision = the 16 s birth-cry wait + rnodeconf (~40 s real; 26 pinned
    # the bar at 95% and read as a stall — live pacing feedback 2026-07-31)
    "erase": 14, "provision": 30, "set_hash": 6, "verify": 8,
    "birth_cry": 10,   # the ember dawn plays during this bar; popup ~ ignition
    "ensure_toolchain": 90, "ensure_source": 30, "build_firmware": 300,
    "write_reticulum_config": 5, "install_software_stack": 180, "configure_services": 20,
    "install_health_reporter": 25, "apply_system_hardening": 10, "set_hostname": 5, "final_verification": 15,
    "birth_certificate": 3,
}
_DEFAULT_STEP_SECONDS = 12

#: Operator-facing label per running step — the busy text tracks the build's
#: actual PHASE (operator spec 2026-07-31: the quiet verification tail after
#: the flash needs its own visible heartbeat, not generic 'working').
_PHASE_LABELS = {
    "detect_board": "Detecting the board…",
    "detect_hardware": "Detecting the hardware…",
    "flash_firmware": "Flashing… keep the board plugged in — do not unplug.",
    "flash_rnode_firmware": "Flashing the radio… keep it plugged in.",
    "flash": "Flashing… keep the board plugged in.",
    "set_firmware_radio_parameters": "Setting radio parameters…",
    "set_params": "Setting radio parameters…",
    "wifi_onboarding": "Configuring the node over its setup WiFi… the medic "
                       "may briefly leave your WiFi and rejoin.",
    "verify_beacon": "Verifying… listening for the node's first health beacon "
                     "(up to a minute of quiet is normal — wait for the green "
                     "confirmation).",
    "install_software_stack": "Installing the software stack… (minutes on a "
                              "fresh Pi).",
    "configure_services": "Starting the node's services…",
    "install_health_reporter": "Installing the health reporter…",
    "apply_system_hardening": "Hardening the system…",
    "set_hostname": "Setting the hostname…",
    "final_verification": "Verifying the node…",
    "birth_certificate": "Writing the birth certificate…",
    "birth_cry": "The birth cry — watch the node's light show.",
}


def _workflow_step_names(wf):
    """Ordered step names of a build workflow (for progress weighting and the
    pre-listed checklist). Falls back to the RNode-flash order when a workflow
    exposes neither ``planned_step_names()`` nor ``.steps``."""
    planner = getattr(wf, "planned_step_names", None)
    if planner is not None:
        try:
            return list(planner())
        except Exception:
            pass
    steps = getattr(wf, "steps", None)
    if steps:
        try:
            return [n for n, _ in steps]
        except Exception:
            pass
    return ["detect_port", "ensure_single_board", "ensure_firmware", "flash", "set_params"]


class _StepBar(Widget):
    """A slim horizontal loading bar for one checklist step — fills as the
    step runs, lands full (green/red) when it finishes."""

    def __init__(self, **kwargs):
        kwargs.setdefault("size_hint", (1, None))
        kwargs.setdefault("height", dp(9))
        super().__init__(**kwargs)
        self._frac = 0.0
        self._color = "accent"
        self.bind(pos=self._draw, size=self._draw)

    def set(self, frac, color=None):
        self._frac = max(0.0, min(1.0, frac))
        if color is not None:
            self._color = color
        self._draw()

    def _draw(self, *_):
        from kivy.graphics import Color, RoundedRectangle
        self.canvas.clear()
        with self.canvas:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            RoundedRectangle(pos=self.pos, size=self.size,
                             radius=[dp(4)] * 4)
            if self._frac > 0.02:
                Color(*theme.hex_to_rgba(theme.COLORS[self._color]))
                RoundedRectangle(
                    pos=self.pos,
                    size=(max(dp(8), self.width * self._frac), self.height),
                    radius=[dp(4)] * 4)


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
                 on_mitosis=None, prefill_location=None, on_guide=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        # (lat, lon, source) stamped from the map's "Use this position", or None.
        self._prefill_location = prefill_location
        # on_guide() — open the step-by-step guided birth (for a new operator).
        self._on_guide = on_guide
        # When arriving from the guide with a chosen kind, don't let auto-detect
        # flip the firmware family out from under the operator (cleared on a manual
        # firmware tap). None = detection decides (the 'radio' path).
        self._forced_firmware = None
        self._saved_cert_id = None
        # Step one is naming the NEW node. Created once and re-parented on each header
        # rebuild so a typed name survives board changes. (Existing nodes are reached
        # from VITALS/SCAN -> their certificate card, which offers Triage.) Notes are
        # asked at the END (after the cert).
        self._name_in = TextInput(hint_text="Name this node  (e.g. Rooftop-East)",
                                  multiline=False, size_hint_y=None, height=dp(46),
                                  font_size="16sp")
        self._end_notes_in = TextInput(
            hint_text="Notes  (optional — mast height, landmarks…)",
            multiline=True, size_hint_y=None, height=dp(70), font_size="15sp")
        bind_field(self._name_in)
        bind_field(self._end_notes_in)
        # Live-render the typed name onto the board photo's on-screen display.
        self._name_in.bind(text=lambda *_: self._update_board_cards())
        self._rtnode_cards = {}                        # target key -> BoardCard
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

    def _build_chooser(self, keep_list=False):
        """The hardware chooser: a Board picker (required) and a Host Pi picker
        (optional — board-only is a standalone radio). Each opens a numbered,
        scrollable list; a Spinner dropdown flickered shut on this panel.
        keep_list=True restores the chooser header WITHOUT wiping the scroll
        below (used after a failed build so the log stays readable)."""
        self.header.clear_widgets()
        if hasattr(self, "list") and not keep_list:
            self.list.clear_widgets()
        self.header.add_widget(_line("Birth a new node", bold=True, size="22sp"))

        # The step-by-step guide entry lives at the BOTTOM as a modest link —
        # a big green button at the top read as 'continue' and yanked operators
        # back to the guide's start mid-birth (2026-07-30 report).

        # Step one: name the NEW node being built. (Existing nodes live in VITALS /
        # SCAN — tap one to open its certificate, which offers Triage.)
        self.header.add_widget(_line("Name this node", bold=True, size="15sp",
                                     color="accent"))
        self.header.add_widget(self._name_in)
        if self._prefill_location:
            lat, lon, src = self._prefill_location
            self.header.add_widget(_line(
                f"Location stamped: {lat:.5f}, {lon:.5f}  (from {src})",
                size="12.5sp", color="green"))

        self.header.add_widget(Widget(size_hint_y=None, height=dp(12)))

        if not self._firmware:
            # ENTRY POINT — nothing chosen yet: detect the board and/or pick a
            # firmware family. Once chosen, these collapse (see the else branch).
            # The Detect button earns its place only while there's detecting
            # left to do — once a board is FOUND, the green summary + the
            # become-ask say it all (operator spec 2026-08-01: 'the board's
            # already been connected').
            if not (self._detected or {}).get("found"):
                self.header.add_widget(_line("Choose your hardware:", size="13sp",
                                             color="text_secondary"))
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
            det = self._detected or {}
            det_opts = det.get("firmware") if det.get("found") else None
            if det_opts and len(det_opts) > 1:
                # Detection NARROWED but couldn't decide — ask with the full
                # labels so the operator picks the family deliberately
                # (auto-leading with RTNode-2400 built the wrong firmware —
                # Tern1 incident, 2026-07-31).
                self.header.add_widget(_line(
                    "What should this board become?", bold=True, size="15sp",
                    color="accent"))
                if "rnode" in det_opts and "pi_rnode" not in det_opts:
                    # any RNode-capable board can also be a Pi's radio
                    det_opts = list(det_opts) + ["pi_rnode"]
                # Uniform family order EVERYWHERE (operator spec 2026-07-31):
                # RNode -> RTNode-2400 -> Pi + RNode.
                _rank = {"rnode": 0, "rtnode2400": 1, "pi_rnode": 2}
                det_opts = sorted(det_opts, key=lambda k: _rank.get(k, 99))
                for key in det_opts:
                    b = Button(text=FIRMWARE_LABEL.get(key, key),
                               size_hint_y=None, height=dp(54), halign="left",
                               font_size="14.5sp", bold=True,
                               background_normal="",
                               background_color=theme.hex_to_rgba(
                                   theme.COLORS["surface"]),
                               color=theme.hex_to_rgba(
                                   theme.COLORS["text_primary"]))
                    b.bind(size=lambda i, v: setattr(
                        i, "text_size", (v[0] - dp(20), v[1])))
                    b.bind(on_release=lambda _b, k=key: self._pick_firmware(k))
                    self.header.add_widget(b)
            else:
                self.header.add_widget(_line("Firmware", bold=True, size="15sp",
                                             color="accent"))
                fw_row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                   height=dp(50), spacing=dp(6))
                for key, short in (("rnode", "RNode"),
                                   ("rtnode2400", "RTNode-2400"),
                                   ("pi_rnode", "Pi + RNode")):
                    b = Button(text=short, font_size="14sp", bold=True,
                               background_normal="",
                               background_color=theme.hex_to_rgba(
                                   theme.COLORS["surface"]),
                               color=theme.hex_to_rgba(
                                   theme.COLORS["text_primary"]))
                    b.bind(on_release=lambda _b, k=key: self._pick_firmware(k))
                    fw_row.add_widget(b)
                self.header.add_widget(fw_row)
        else:
            # CHOSEN (via detect or the guided flow): detection + firmware are DONE,
            # so collapse them to one line with a 'change' escape and go straight to
            # confirming the board.
            self.header.add_widget(self._firmware_summary_row())

        if self._firmware == "rtnode2400":
            self._add_rtnode_confirm()
        elif self._firmware in ("rnode", "pi_rnode"):
            self.header.add_widget(_line("Board (radio)", bold=True, size="15sp",
                                         color="accent"))
            if self._sel_board is None:
                self._add_rnode_board_pick()
            else:
                self.header.add_widget(self._sel_button(
                    self._sel_board.display_name, self._choose_board))
            if self._firmware == "pi_rnode":
                self.header.add_widget(Widget(size_hint_y=None, height=dp(10)))
                self.header.add_widget(_line("Host Pi", bold=True, size="15sp",
                                             color="accent"))
                self.header.add_widget(self._sel_button(
                    self._sel_pi[1] if self._sel_pi else "Tap to choose a Pi",
                    self._choose_pi))
                # The REAL provision path SSHes to the Pi — it needs an address
                # (and the login user; fresh RPi OS images default to 'pi').
                self.header.add_widget(_line(
                    "Pi address on your network  (the SD-imaging step sets the "
                    "medic's SSH key on the Pi)", size="12.5sp",
                    color="text_secondary"))
                row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                height=dp(48), spacing=dp(8))
                if not hasattr(self, "_pi_addr_in"):
                    from ui.onscreen_keyboard import bind_field
                    from kivy.uix.textinput import TextInput
                    self._pi_addr_in = bind_field(TextInput(
                        text="", multiline=False, font_size="15sp",
                        hint_text="raspberrypi.local or 192.168.1.50"))
                    self._pi_user_in = bind_field(TextInput(
                        text="pi", multiline=False, font_size="15sp",
                        hint_text="user", size_hint_x=0.3))
                for w_ in (self._pi_addr_in, self._pi_user_in):
                    if w_.parent is not None:
                        w_.parent.remove_widget(w_)
                row.add_widget(self._pi_addr_in)
                row.add_widget(self._pi_user_in)
                self.header.add_widget(row)

        # (Mitosis moved out of this chooser — it lives at the start of the
        # flow where the operator picks what they're doing; repeating it here
        # cluttered the board-confirm page. Operator decision 2026-07-31.)
        # The scroll below shows the next action for the chosen firmware.
        if hasattr(self, "list") and not keep_list:
            self._build_action()

    def _enter_flash_view(self, title):
        """A DISTINCT flashing page: the chooser header (name field, V3/V4
        cards) is swapped out for the whole build, so the operator isn't left
        staring at 'select your board' while a flash runs (operator spec
        2026-07-31). Restored by _exit_flash_view when the outcome is read."""
        self._flash_view = True
        try:
            # The name field's keypad has no business on the flash page.
            from kivy.app import App
            kb = getattr(App.get_running_app(), "keyboard", None)
            if kb is not None:
                kb.hide()
        except Exception:
            pass
        self.header.clear_widgets()
        self.header.add_widget(_line(title, bold=True, size="20sp",
                                     color="accent"))
        self.header.add_widget(_line(
            "The board will restart itself during the flash — its screen and "
            "LED may blink, and it may vanish from USB for a few seconds. "
            "That's normal. Keep it plugged in.",
            size="13sp", color="text_secondary"))

    def _exit_flash_view(self, keep_list=False):
        if getattr(self, "_flash_view", False):
            self._flash_view = False
            self._build_chooser(keep_list=keep_list)

    def _build_action(self):
        """Populate the scroll with the next step for the chosen firmware: the
        RTNode-2400 build button, the RNode radio-params form, or a prompt."""
        self.list.clear_widgets()
        if self._firmware == "rtnode2400":
            # The Build button itself lives in the HEADER right under the board
            # cards (always visible on the 5" screen — the scroll area below can
            # be starved to zero height by the tall header). Only the extra
            # explainer goes here.
            if self._rtnode_target:
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
                            lambda b=board: self._confirm_rnode_board_gate(b)))
        self._picker_popup("Select the board", entries)

    def _pick_board(self, board):
        self._sel_board = board
        self._build_chooser()

    def _add_rnode_board_pick(self):
        """The RNode board pick, RTNode-style: detection narrows the catalogue
        to boards matching the read chip and OFFERS them right here — photo
        cards where we have photos, wide buttons otherwise (operator spec
        2026-07-31: the old blank 'tap to choose' hid the choice in a popup).
        No detection -> the full-list picker button, as before."""
        det = self._detected or {}
        shortlist = det.get("boards") or []
        if not det.get("found") or not shortlist:
            self.header.add_widget(self._sel_button("Tap to choose a board",
                                                    self._choose_board))
            return
        self.header.add_widget(_line(
            f"Detected {det.get('platform') or det.get('chip')} — which board "
            "is this?", size="13sp", color="text_secondary"))
        from ui import board_images
        from ui.widgets.board_card import BoardCard
        # image_for = the photo FILE exists (registry entries are placeholders
        # until each board's PNG arrives — they stay text buttons meanwhile)
        with_photo = [b for b in shortlist if board_images.image_for(b.key)]
        # Rank the medic's OWN fleet first: board types it has birthed before
        # (from stored certs) lead the cards — a field medic mostly re-meets
        # its own hardware (operator spec 2026-08-01: thin the crowd; among
        # native-USB S3s the hardware is genuinely indistinguishable, so
        # ranking + readable cards is the honest thinning).
        try:
            from ui.cert_store import load_certs
            seen = {c.get("board") for c in load_certs()}
            with_photo.sort(key=lambda b: b.display_name not in seen)
        except Exception:
            pass
        # rows of 3 — five cards in one strip were unreadable thumbnails
        for i in range(0, len(with_photo), 3):
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(120), spacing=dp(10))
            for b in with_photo[i:i + 3]:
                row.add_widget(BoardCard(
                    b.key, name=self._name_in.text.strip(),
                    on_select=lambda bb=b: self._confirm_rnode_board_gate(bb)))
            for _ in range(3 - len(with_photo[i:i + 3])):
                row.add_widget(Widget())     # keep card widths consistent
            self.header.add_widget(row)
        for b in shortlist:
            if b in with_photo:
                continue
            self.header.add_widget(self._sel_button(
                b.display_name,
                lambda bb=b: self._confirm_rnode_board_gate(bb)))
        other = self._sel_button("Not one of these — full board list",
                                 self._choose_board)
        other.height = dp(40)
        other.font_size = "13sp"
        other.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
        self.header.add_widget(other)

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
        try:
            # The chip-id read resets the board (USB re-enumerates) — don't
            # let the disconnect watch false-alarm on our own detect.
            from kivy.app import App
            App.get_running_app().quiet_board_watch(40)
        except Exception:
            pass
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
                opts = res.get("firmware") or ["rnode"]
                # Auto-pick ONLY when the chip has a single possibility. An
                # S3 can be RTNode-2400 OR RNode — silently leading with
                # RTNode built the wrong firmware for an operator who wanted
                # an RNode (Tern1 incident, 2026-07-31). Ambiguity -> ASK.
                self._firmware = opts[0] if len(opts) == 1 else None
            if self._firmware == "rtnode2400":
                # a chip read can't tell V3 from V4 — the operator CONFIRMS via the
                # board photos, so don't pre-pick a target here.
                pass
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
        # DEAD-END the RTNode path outright for boards it can't build — a
        # flash would succeed and produce a nonfunctional node (operator
        # spec 2026-08-01: popup, confirm goes HOME).
        if key == "rtnode2400" and self._rtnode_blocked_board():
            self._block_rtnode_deadend()
            return
        self._firmware = key
        self._forced_firmware = None            # a manual tap is an explicit override
        self._build_chooser()

    def _rtnode_blocked_board(self):
        """The plugged board's recorded name when it's hardware RTNode-2400
        has no build for (fingerprint-recognised); None when unknown or OK."""
        try:
            port = (self._detected or {}).get("port")
            if not port:
                return None
            from ui.hw_factories import LocalConnection
            from workflows.rnode_flash import usb_id_for_port
            from ui.cert_store import load_certs
            usb = usb_id_for_port(LocalConnection(), port)
            known = next((c for c in load_certs()
                          if usb and c.get("usb_serial") == usb), None)
            supported = ("Heltec LoRa32 v3", "Heltec LoRa32 v4")
            if known and known.get("board") not in supported:
                return known.get("board")
        except Exception:
            pass
        return None

    def _block_rtnode_deadend(self):
        """The popup dead-end: no choices, confirm goes home."""
        if getattr(self, "_rtblock_pop", None) is not None:   # open-once
            return
        from ui.requirement_popup import requirement_popup
        view = requirement_popup(
            "This board cannot currently be flashed as an RTNode-2400.",
            "Not available for this board", False)
        self._rtblock_pop = view

        def _home(*_a):
            self._rtblock_pop = None
            try:
                from kivy.app import App
                app = App.get_running_app()
                if app is not None and hasattr(app, "switch_mode"):
                    app.switch_mode("home")
            except Exception:
                pass
        view.bind(on_dismiss=_home)

    def _reset_firmware(self):
        """'change' — back out of the chosen firmware so the operator can re-detect
        or pick a different family (the escape hatch from the collapsed summary)."""
        self._firmware = None
        self._forced_firmware = None
        self._rtnode_target = None
        self._rtnode_cards = {}
        self._sel_board = None       # a board picked under the OLD family is stale
        self._build_chooser()

    def _firmware_summary_row(self):
        """A one-line 'detected X · <firmware>  [change]' summary, shown once the
        firmware is chosen so the detect button + firmware picker don't clutter the
        confirm step (they were the PREVIOUS step)."""
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(34),
                        spacing=dp(8))
        d = self._detected or {}
        fam = FIRMWARE_LABEL.get(self._firmware, self._firmware).split("  ")[0]
        if d.get("found"):
            txt = (f"Detected {d.get('platform', d.get('chip', 'board'))} on "
                   f"{d.get('port', 'USB')}  ·  {fam}")
        else:
            txt = fam
        row.add_widget(_line(txt, size="12.5sp", color="green"))
        change = Button(text="change", size_hint=(None, 1), width=dp(84),
                        font_size="13sp", bold=True, background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["accent"]))
        change.bind(on_release=lambda *_: self._reset_firmware())
        row.add_widget(change)
        return row

    def _warning_box(self, text):
        """A hazard box — yellow-tinted fill + red outline + bold text — for the
        can-brick warning, so it reads as a warning box, not just coloured text."""
        from kivy.graphics import Color, RoundedRectangle, Line
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=[dp(12), dp(10)])
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(_line(text, size="13.5sp", color="warning_yellow", bold=True))
        with box.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"], 0.18))
            rect = RoundedRectangle(radius=[dp(8)] * 4)
            Color(*theme.hex_to_rgba(theme.COLORS["red"]))
            outline = Line(width=dp(2.2))

        def _sync(*_):
            rect.pos, rect.size = box.pos, box.size
            outline.rounded_rectangle = (box.x, box.y, box.width, box.height, dp(8))
        box.bind(pos=_sync, size=_sync)
        _sync()
        return box

    def _add_rtnode_confirm(self):
        # DEAD-END: a fingerprint-recognised board RTNode-2400 can't build
        # (a Wireless Tracker walked into these V3/V4 cards 2026-08-01 —
        # either image would flash but land on wrong pins). Popup, no cards,
        # confirm goes home (operator spec).
        if self._rtnode_blocked_board():
            self._block_rtnode_deadend()
            return
        """V3/V4 board-photo chooser: the two Heltec boards look identical to Node
        Medic over USB, so the operator taps the one in front of them. The typed
        node name renders live on each board's little screen."""
        from ui.widgets.board_card import BoardCard
        from ui import board_images
        self.header.add_widget(_line(
            "Which board is it?  V3 and V4 look identical to Node Medic — tap the one "
            "in front of you (the silkscreen says V3 or V4).", size="13.5sp",
            color="accent"))
        self.header.add_widget(self._warning_box(
            "WARNING:  Selecting the wrong board can brick the hardware. Check your "
            "selection before you build."))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(178),
                        spacing=dp(10))
        self._rtnode_cards = {}
        nm = self._name_in.text.strip()
        for key in ("heltec_v3", "heltec_v4"):
            col = BoxLayout(orientation="vertical", spacing=dp(4))
            sel = (self._rtnode_target == key)
            # the name only shows on the board you've PICKED — not both.
            card = BoardCard(key, name=(nm if sel else ""), selected=sel,
                             on_select=lambda k=key: self._pick_rtnode_target(k),
                             size_hint_y=1)
            self._rtnode_cards[key] = card
            col.add_widget(card)
            lbl = _line(board_images.label(key), bold=True, size="16sp",
                        color="accent" if self._rtnode_target == key else "text_primary")
            lbl.halign = "center"
            lbl.size_hint_y = None
            lbl.height = dp(26)
            col.add_widget(lbl)
            row.add_widget(col)
        self.header.add_widget(row)
        # Tapping a board no longer arms a Build button directly — it opens a
        # full CONFIRMATION gate (operator spec 2026-07-31: too easy to skim
        # past the brick warning and build for the wrong board). The gate shows
        # the warning LARGE, the chosen board in the middle, and an explicit
        # confirm that then starts the build.
        self.header.add_widget(_line(
            "Tap the board in front of you — you'll confirm it before "
            "anything builds.", size="12.5sp", color="text_secondary"))
        # T-Beam Supreme (SD transport) is a rarer RTNode target — keep it reachable
        # without cluttering the common V3/V4 choice.
        other = Button(text="Other RTNode board (T-Beam Supreme)…", size_hint_y=None,
                       height=dp(38), font_size="12.5sp", background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        other.bind(on_release=lambda *_: self._choose_rtnode_target())
        self.header.add_widget(other)

    def _update_board_cards(self):
        """Push the current name onto any live board cards (called as the operator
        types), so it appears on the board's screen in real time."""
        nm = self._name_in.text.strip()
        for key, card in getattr(self, "_rtnode_cards", {}).items():
            try:
                card.set_name(nm if self._rtnode_target == key else "")
            except Exception:
                pass

    def begin_guided(self, path):
        """Arrived from the step-by-step guide. Pre-scope the firmware for the chosen
        kind (radio = let detection decide; host = RNode; pi = Pi + RNode) and
        auto-run detection, since the board is already plugged in per the guide — so
        the operator lands on naming + a suggested setup, not a cold form."""
        # FRESH LAP: this screen is reused, and a stale _sel_board from the
        # previous build silently SKIPPED the board pick + confirm gate and
        # offered the last lap's board (a V3 nearly flashed as 'Heltec V4' —
        # caught live 2026-08-01). Nothing selection-shaped survives.
        self._sel_board = None
        self._sel_pi = None
        self._detected = None
        self._rtnode_target = None
        self._firmware = None
        # A map-stamped position belongs to ONE node: left set, every later
        # birth in the session inherited the previous node's coordinates
        # (2026-08-01 bug hunt — a privacy leak as well as a wrong pin).
        self._prefill_location = None
        self._forced_firmware = {"radio": "rtnode2400", "host": "rnode",
                                 "pi": "pi_rnode"}.get(path)
        if self._forced_firmware:
            self._firmware = self._forced_firmware
        self._build_chooser()
        self._detect_board()

    def _choose_rtnode_target(self):
        entries = [(i, t.display, lambda k=key: self._pick_rtnode_target(k))
                   for i, (key, t) in enumerate(RTNODE_TARGETS.items(), 1)]
        self._picker_popup("RTNode-2400 target", entries)

    def _pick_rtnode_target(self, key):
        self._rtnode_target = key
        self._build_chooser()
        self._confirm_board_gate(key)

    def _confirm_board_gate(self, key):
        """Full-screen confirmation between picking a board and building it:
        the brick warning ENLARGED (yellow on red-outline, requirement style),
        the chosen board pictured in the middle, and an explicit confirm that
        launches the build. V3 and V4 look identical over USB — flashing the
        wrong image bricks boards (operator spec 2026-07-31)."""
        # open-once guard (doubled-touch stacks two gates otherwise)
        if getattr(self, "_gate_pop", None) is not None:
            return
        from ui.widgets.board_card import BoardCard
        from ui import board_images
        tgt = RTNODE_TARGETS[key]
        yellow = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
        dark = theme.hex_to_rgba(theme.COLORS["background"])
        body = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        warn = Label(
            text=("WARNING:  Selecting the wrong board can BRICK the "
                  "hardware.\nCheck the silkscreen on the board itself."),
            bold=True, font_size="19sp", color=dark, halign="center",
            valign="middle", size_hint_y=None, height=dp(92))
        warn.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(warn)
        card = BoardCard(key, name=self._name_in.text.strip(), selected=True,
                         on_select=lambda *_: None, size_hint_y=1)
        body.add_widget(card)
        confirm_lbl = Label(
            text=f"Confirm you've selected the correct board:  "
                 f"{board_images.label(key)}",
            bold=True, font_size="16sp", color=dark, halign="center",
            valign="middle", size_hint_y=None, height=dp(44))
        confirm_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(confirm_lbl)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(58), spacing=dp(10))
        back = Button(text="Back — wrong board", bold=True, font_size="15sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        go = Button(text=f"Confirm — build RTNode-2400\n({tgt.display})",
                    bold=True, font_size="15sp", halign="center",
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=dark)
        row.add_widget(back)
        row.add_widget(go)
        body.add_widget(row)
        with body.canvas.before:
            from kivy.graphics import Color, Rectangle, Line
            Color(*yellow)
            rect = Rectangle()
            Color(*theme.hex_to_rgba(theme.COLORS["red"]))
            border = Line(width=dp(2))

        def _sync(*_):
            rect.pos, rect.size = body.pos, body.size
            border.rectangle = (body.x + dp(2), body.y + dp(2),
                                body.width - dp(4), body.height - dp(4))
        body.bind(pos=_sync, size=_sync)
        pop = Popup(title="Check the board", content=body, size_hint=(0.95, 0.9),
                    title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    auto_dismiss=False)
        self._gate_pop = pop
        pop.bind(on_dismiss=lambda *_: setattr(self, "_gate_pop", None))
        back.bind(on_release=lambda *_: pop.dismiss())

        def _go(btn, *_a):
            btn.disabled = True
            btn.text = "Starting build…"
            pop.dismiss()
            self._run_rtnode()
        go.bind(on_release=_go)
        pop.open()

    def _confirm_rnode_board_gate(self, board):
        """The SAME check-the-board gate as RTNode-2400, for the RNode/Pi
        paths (operator spec 2026-07-31): after tapping a board, that ONE
        board comes up enlarged with the brick warning and an explicit
        confirm — only then on to the radio-params form."""
        if getattr(self, "_gate_pop", None) is not None:   # doubled-tap guard
            return
        from ui.widgets.board_card import BoardCard
        from ui import board_images
        yellow = theme.hex_to_rgba(theme.COLORS["warning_yellow"])
        dark = theme.hex_to_rgba(theme.COLORS["background"])
        body = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        warn = Label(
            text=("WARNING:  Selecting the wrong board can BRICK the "
                  "hardware.\nCheck the silkscreen on the board itself."),
            bold=True, font_size="19sp", color=dark, halign="center",
            valign="middle", size_hint_y=None, height=dp(92))
        warn.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(warn)
        if board_images.image_for(board.key):
            body.add_widget(BoardCard(board.key,
                                      name=self._name_in.text.strip(),
                                      selected=True,
                                      on_select=lambda *_: None,
                                      size_hint_y=1))
        else:                                  # no photo — the name, writ large
            big = Label(text=board.display_name, bold=True, font_size="26sp",
                        color=dark, halign="center", valign="middle",
                        size_hint_y=1)
            big.bind(size=lambda w, s: setattr(w, "text_size", s))
            body.add_widget(big)
        confirm_lbl = Label(
            text=f"Confirm you've selected the correct board:  "
                 f"{board.display_name}",
            bold=True, font_size="16sp", color=dark, halign="center",
            valign="middle", size_hint_y=None, height=dp(44))
        confirm_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(confirm_lbl)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(58), spacing=dp(10))
        back = Button(text="Back — wrong board", bold=True, font_size="15sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        go = Button(text=f"Confirm — flash as RNode\n({board.display_name})",
                    bold=True, font_size="15sp", halign="center",
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=dark)
        row.add_widget(back)
        row.add_widget(go)
        body.add_widget(row)
        with body.canvas.before:
            from kivy.graphics import Color, Rectangle, Line
            Color(*yellow)
            rect = Rectangle()
            Color(*theme.hex_to_rgba(theme.COLORS["red"]))
            border = Line(width=dp(2))

        def _sync(*_):
            rect.pos, rect.size = body.pos, body.size
            border.rectangle = (body.x + dp(2), body.y + dp(2),
                                body.width - dp(4), body.height - dp(4))
        body.bind(pos=_sync, size=_sync)
        pop = Popup(title="Check the board", content=body, size_hint=(0.95, 0.9),
                    title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    auto_dismiss=False)
        self._gate_pop = pop
        pop.bind(on_dismiss=lambda *_: setattr(self, "_gate_pop", None))
        back.bind(on_release=lambda *_: pop.dismiss())

        def _go(btn, *_a):
            btn.disabled = True
            pop.dismiss()
            self._pick_board(board)            # -> radio-params form
        go.bind(on_release=_go)
        pop.open()

    def _run_rtnode(self):
        """Kick off the real RTNode-2400 build on the attached board (or honest-fail
        with why, if it can't run)."""
        # The outcome panel reads these; a previous RNode lap's board made an
        # RTNode build report as an RNode flash (2026-08-01 bug hunt).
        self._last_board = None
        self._last_type = "rtnode2400"
        workflow = self._factories["rtnode2400"](self._rtnode_target,
                                                 self._name_in.text.strip())
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
        self._param_edit_warned = False      # fresh form -> warn on first edit
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
        # The FIRST touch on any param field gets the strong keep-the-presets
        # warning (operator spec 2026-07-31 — the OK-start check alone let you
        # edit away without ever being told).
        ti.bind(focus=lambda inst, focused:
                self._warn_param_edit(inst) if focused else None)
        self._param_inputs[key] = ti
        row.add_widget(ti)
        return row

    def _warn_param_edit(self, field):
        """Once per params form: warn the moment the operator taps INTO a
        radio-param field — keep the presets (unfocus) or edit anyway."""
        if getattr(self, "_param_edit_warned", False):
            return
        if getattr(self, "_pw_pop", None) is not None:    # doubled-tap guard
            return
        self._param_edit_warned = True
        from provisioning import radio_defaults as rd
        from kivy.uix.label import Label
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        msg = Label(halign="center", valign="middle", markup=True, text=(
            "[b]You're about to change the radio parameters.[/b]\n\n"
            "It is STRONGLY advised to keep the preset parameters\n"
            f"[b]{rd.summary(rd.load_defaults())}[/b]\n"
            "so that ALL nodes can talk to each other. A node built on "
            "different settings CANNOT hear the rest of the mesh.\n\n"
            "To change the settings every build uses, do it once in\n"
            "Settings ▸ Default radio parameters."),
            color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(8))
        popup = Popup(title="Changing radio parameters", content=box,
                      size_hint=(0.94, 0.7),
                      title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                      separator_color=theme.hex_to_rgba(theme.COLORS["red"]))
        self._pw_pop = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_pw_pop", None))
        keep = Button(text="Keep presets", bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _keep(*_):
            popup.dismiss()
            try:
                field.focus = False           # back away from the field
            except Exception:
                pass
        keep.bind(on_release=_keep)
        edit = Button(text="⚠  Edit anyway", bold=True,
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        edit.bind(on_release=lambda *_: popup.dismiss())
        row.add_widget(edit)                  # danger bottom-left
        row.add_widget(keep)                  # safe bottom-right
        box.add_widget(row)
        popup.open()

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
        """OK — start. Non-standard radio params get the SAME strong warning as
        Settings (operator spec 2026-07-31) — a node built off-standard can't
        hear the rest of the mesh. Then the blocked/power checks, then build."""
        from provisioning import radio_defaults as rd
        radio = self._read_params()
        if not rd.is_standard(radio):
            self._warn_nonstandard_params(
                radio, lambda: self._start_build(node_type, board))
            return
        self._start_build(node_type, board)

    def _warn_nonstandard_params(self, radio, proceed):
        """Keep standard (fills the form back to standard, then builds) vs
        ⚠ Use anyway (builds with the custom values)."""
        if getattr(self, "_nsp_pop", None) is not None:   # doubled-tap guard
            return
        from provisioning import radio_defaults as rd
        box = BoxLayout(orientation="vertical", spacing=dp(10), padding=dp(12))
        from kivy.uix.label import Label
        msg = Label(halign="center", valign="middle", markup=True, text=(
            "[b]Keep the standard parameters?[/b]\n\n"
            "It is STRONGLY recommended to build every node with the standard "
            "settings\n[b]" + rd.summary(rd.DEFAULT_PARAMS) + "[/b]\n"
            "so that ALL nodes can communicate with each other.\n\n"
            "A node built with different parameters CANNOT hear the rest of "
            "the mesh.\n\nYou entered:\n[b]" + rd.summary(radio) + "[/b]"),
            color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(8))
        popup = Popup(title="Non-standard radio parameters", content=box,
                      size_hint=(0.94, 0.8),
                      title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                      separator_color=theme.hex_to_rgba(theme.COLORS["red"]))
        self._nsp_pop = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_nsp_pop", None))

        def _keep(*_):
            popup.dismiss()
            for key, v in rd.DEFAULT_PARAMS.items():   # form back to standard
                ti = self._param_inputs.get(key)
                if ti is not None:
                    ti.text = f"{v:g}" if key in ("freq", "bw") else str(int(v))
            proceed()
        keep = Button(text="Keep standard", bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        keep.bind(on_release=_keep)

        def _anyway(*_):
            popup.dismiss()
            proceed()
        anyway = Button(text="⚠  Use anyway", bold=True, background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        anyway.bind(on_release=_anyway)
        row.add_widget(anyway)                        # danger bottom-left
        row.add_widget(keep)                          # safe bottom-right
        box.add_widget(row)
        popup.open()

    def _start_build(self, node_type, board):
        """The blocked / power checks, then the build (params already vetted)."""
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
            workflow = self._factories["pi_rnode"](
                getattr(self, "_pi_addr_in", None) and self._pi_addr_in.text or "",
                getattr(self, "_pi_user_in", None) and self._pi_user_in.text or "pi",
                self._name_in.text.strip())
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
        # Never run two flashes at once — a second pio/esptool on the same board
        # (or the wrong port) corrupts the write. Refuse if one is already running.
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is not None and app.flash_in_progress():
                label, elapsed = app.activity_info()
                # The touchscreen can deliver a tap TWICE (double input
                # providers) — the duplicate arrives within moments and used to
                # raise a scary 'phantom flash' warning for the build the SAME
                # tap just started (the 2026-07-30 saga). Absorb it silently.
                if elapsed < 10:
                    return
                from ui.requirement_popup import requirement_popup
                requirement_popup(
                    f"{label}\n\nIt has been running {int(elapsed)}s — this is "
                    "the build YOU started (the red banner at the top). Let it "
                    "finish before starting another.",
                    "Your build is running", False)
                return
        except Exception:
            pass
        self._enter_flash_view(title)          # the build gets its OWN page
        self.list.clear_widgets()
        # A progress RING that FILLS with a % as the build advances — determinate,
        # not a scary indeterminate spinner. Weighted by estimated time per step
        # (the flash dominates), and ticked so it climbs during the long compile.
        import time
        from kivy.uix.anchorlayout import AnchorLayout
        from ui.widgets.progress_ring import ProgressRing
        # A BIG centred ring — this page's whole job is the flash, so the
        # progress is the centrepiece (operator spec 2026-07-31).
        self._build_ring = ProgressRing(size=(dp(150), dp(150)),
                                        label_font_size="28sp")
        ring_anchor = AnchorLayout(anchor_x="center", anchor_y="center",
                                   size_hint_y=None, height=dp(170))
        ring_anchor.add_widget(self._build_ring)
        self._build_busy = BoxLayout(orientation="vertical", size_hint_y=None,
                                     spacing=dp(8), padding=[0, dp(8)])
        self._build_busy.bind(
            minimum_height=self._build_busy.setter("height"))
        self._build_busy.add_widget(ring_anchor)
        self._busy_label = _line(
            "Working… the firmware compile is the slow part (a first build also "
            "downloads the toolchain). Keep the board plugged in and WAIT for "
            "the green 'Build finished' confirmation before touching anything.",
            size="13sp", color="accent")
        self._busy_label.halign = "center"
        self._build_busy.add_widget(self._busy_label)
        self.list.add_widget(self._build_busy)
        self._pg_names = _workflow_step_names(workflow)
        # The whole journey listed UP FRONT: every step in grey with its own
        # loading bar; the running step's bar fills, and a finished step's
        # text goes green with a full bar (operator spec 2026-07-31 — steps
        # popping into existence one at a time hid what was still to come).
        self._step_rows = {}
        from kivy.uix.anchorlayout import AnchorLayout
        for n in self._pg_names:
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(28), spacing=dp(12))
            lbl = _line("  " + n, color="text_secondary", size="13.5sp")
            lbl.size_hint_x = 0.5
            bar = _StepBar()
            holder = AnchorLayout(anchor_y="center", size_hint_x=0.5)
            holder.add_widget(bar)
            row.add_widget(lbl)
            row.add_widget(holder)
            self.list.add_widget(row)
            self._step_rows[n] = (lbl, bar)
        self._pg_secs = [_STEP_SECONDS.get(n, _DEFAULT_STEP_SECONDS) for n in self._pg_names]
        self._pg_total = max(1.0, float(sum(self._pg_secs)))
        self._pg_done = 0                        # completed step count
        self._pg_step_start = time.monotonic()
        self._build_ring.set_fraction(0.0)
        self._pg_ev = Clock.schedule_interval(self._tick_progress, 0.2)
        self._workflow = workflow
        self._had_failure = False                # reset for this run's outcome
        self._mark_activity(True)                # keep the screensaver off the flash
        threading.Thread(target=self._run, daemon=True).start()

    def _mark_activity(self, on):
        """Tell the app a flash/build is (not) running so the screensaver can't
        cover it and a persistent 'don't power off' banner shows. Best-effort —
        never let it break a build."""
        try:
            from kivy.app import App
            app = App.get_running_app()
            if on:
                nm = self._name_in.text.strip() or "the board"
                app.begin_activity(
                    f"Flashing {nm} — keep it plugged in, don't power off")
            else:
                app.end_activity()
        except Exception:
            pass

    def _tick_progress(self, _dt):
        ring = getattr(self, "_build_ring", None)
        if ring is None:
            return False                         # unschedule
        import time
        done = sum(self._pg_secs[:self._pg_done])
        if self._pg_done < len(self._pg_secs):   # creep across the running step
            cur = self._pg_secs[self._pg_done]
            elapsed = time.monotonic() - self._pg_step_start
            done += cur * min(0.97, elapsed / max(1.0, cur))
            # the running step's own checklist bar creeps too
            pair = getattr(self, "_step_rows", {}).get(
                self._pg_names[self._pg_done])
            if pair is not None:
                pair[1].set(min(0.95, elapsed / max(1.0, cur)))
        # Cap at 95% until _finish() truly lands — a ring that hits full while
        # verification still runs reads as 'done' and invites unplugging
        # (operator feedback 2026-07-31: dead air between full ring and the
        # finished popup).
        ring.set_fraction(min(0.95, done / self._pg_total))

    def _stop_build_progress(self):
        ev = getattr(self, "_pg_ev", None)
        if ev is not None:
            ev.cancel()
            self._pg_ev = None
        ring = getattr(self, "_build_ring", None)
        if ring is not None:
            ring.set_fraction(1.0)               # snap to 100%
        row = getattr(self, "_build_busy", None)
        if row is not None and row.parent:
            self.list.remove_widget(row)
        self._build_busy = self._build_ring = None

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
        # try/finally: a workflow step that RAISES (instead of returning a
        # failed StepResult) must never strand the flash lock — that freezes
        # every future build behind the 'flash already running' popup with no
        # red banner and no outcome (2026-07-30 04:45 incident).
        try:
            self._workflow.run_all(on_progress=lambda r:
                                  Clock.schedule_once(lambda dt: self._step(r), 0))
        except Exception as e:
            self._had_failure = True
            msg = f"{type(e).__name__}: {e}"
            Clock.schedule_once(lambda dt, m=msg: self.list.add_widget(
                _line(f"  [CRASH] {m}", color="red", size="13sp")), 0)
        finally:
            Clock.schedule_once(lambda dt: self._finish(), 0)

    def _step(self, result):
        mark = "skip" if result.skipped else ("ok" if result.success else "FAIL")
        color = ("text_secondary" if result.skipped
                 else "green" if result.success else "red")
        if not result.success and not result.skipped:
            self._had_failure = True
        pair = getattr(self, "_step_rows", {}).get(result.name)
        if pair is not None:
            # pre-listed checklist row: fill the bar, colour the text
            lbl, bar = pair
            lbl.color = theme.hex_to_rgba(theme.COLORS[color])
            lbl.bold = not result.skipped
            if result.skipped:
                lbl.text = f"  {result.name}  (skipped)"
            bar.set(1.0, color=("green" if result.success or result.skipped
                                else "red"))
        else:                                    # unplanned step -> old style
            self.list.add_widget(_line(f"  [{mark}] {result.name}", color=color,
                                       size="14sp"))
        # advance the progress ring past the step that just finished
        if getattr(self, "_pg_secs", None) and self._pg_done < len(self._pg_secs):
            import time
            self._pg_done += 1
            self._pg_step_start = time.monotonic()
            # the busy text tracks the phase now RUNNING — 'verifying…' gets
            # its own visible heartbeat instead of dead air after the flash
            lbl = getattr(self, "_busy_label", None)
            names = getattr(self, "_pg_names", None) or []
            if lbl is not None and self._pg_done < len(names):
                lbl.text = _PHASE_LABELS.get(names[self._pg_done], "Working…")
            if getattr(self, "_build_ring", None) is not None:
                self._build_ring.set_fraction(
                    sum(self._pg_secs[:self._pg_done]) / self._pg_total)
        # Surface the reason on failure — otherwise an honest "not wired yet /
        # plug the board in" message is swallowed and only the step name shows.
        if not result.success and not result.skipped and getattr(result, "message", ""):
            self.list.add_widget(_line(f"      {result.message}", color="amber",
                                       size="12sp"))

    def _popup_outcome(self):
        """Surface the build's outcome as a POPUP the moment it ends. The ring
        vanishing + the result/onboarding info rendering below the fold left the
        operator scrolling to learn whether their build lived or died
        (2026-07-30 report: 'this info should pop up instead')."""
        try:
            from ui.requirement_popup import requirement_popup
            if getattr(self, "_had_failure", False):
                view = requirement_popup(
                    "A build step failed — the [FAIL] line in the build log "
                    "names it, with the reason under it. Fix that and run the "
                    "build again.\n\nBoard won't flash?  Hold BOOT, tap RST, "
                    "release BOOT, retry — or use a short, known-good USB data "
                    "cable.",
                    "Build didn't finish", False)
                # Bring the chooser back so the operator can rerun, but KEEP
                # the log below — it names what failed.
                view.bind(on_dismiss=lambda *_:
                          self._exit_flash_view(keep_list=True))
                return
            onboarding = getattr(self._workflow, "onboarding", None)
            nm = (onboarding or {}).get("node_name", "") or "the node"
            if onboarding:
                view = requirement_popup(
                    f"Build finished for {nm}.\n\nIf its screen still says "
                    "CONFIG MODE, the setup details are printed in the build "
                    "log — join the 'RTNode-Setup' WiFi and enter them at "
                    "http://10.0.0.1. If it shows its status screen, it's "
                    "already configured — watch VITALS for its first health "
                    "beacon.",
                    "Build finished", False, tone="success")
            else:
                view = requirement_popup(
                    "Build finished — details and the birth certificate are in "
                    "the build log below. Watch VITALS for the node's first "
                    "health beacon.",
                    "Build finished", False, tone="success")
            # Dismissing the success card SCROLLS TO THE CERTIFICATE (QR
            # included) — going straight home raced past it (operator spec
            # 2026-08-01: 'let the user see the birth screen with QR code for
            # all births'). The cert's own 'Done — back to home' button ends
            # the ceremony (see _commit_cert).
            def _show_cert(*_a):
                try:
                    from kivy.clock import Clock
                    Clock.schedule_once(lambda dt: self._scroll_to_cert(), 0.3)
                except Exception:
                    pass
            view.bind(on_dismiss=_show_cert)
        except Exception:
            pass

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
        self._mark_activity(False)               # build done -> screensaver allowed again
        self._stop_build_progress()              # build done -> ring to 100%, remove
        self._outcome_panel()
        self._popup_outcome()                    # the outcome comes TO the operator
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
            # GATE: never bake a location into the cert unseen. If this node has
            # a location, confirm it on a map first (catches a wrong/stale GPS fix
            # or a mistyped address before a repair crew drives to the wrong spot).
            self._confirm_location_then_commit(cert)

    def _confirm_location_then_commit(self, cert):
        """Show the map confirm popup for this cert's location, then commit. No
        location -> commit straight away; popup unavailable -> commit as-is."""
        from ui.screens.cert_view_screen import cert_latlon
        ll = cert_latlon(cert)
        if ll is None:
            self._commit_cert(cert)
            return
        try:
            from ui.widgets.confirm_location import ConfirmLocationPopup
            from monitor.geo import splitter_gps_reader

            def _ok(lat, lon):
                cert["location"] = f"{lat:.6f}, {lon:.6f} (confirmed)"
                self._commit_cert(cert)

            def _cancel():
                cert.pop("location", None)    # don't bake an unconfirmed pin
                self._commit_cert(cert)

            ConfirmLocationPopup(
                ll[0], ll[1], node_name=cert.get("node_name", ""),
                on_confirm=_ok, on_cancel=_cancel,
                gps_reader=splitter_gps_reader()).open()
        except Exception:
            self._commit_cert(cert)

    def _commit_cert(self, cert):
        """Persist the (location-confirmed) cert, enrol kin, and render it."""
        from ui.cert_store import save_cert
        # Every certificate records WHEN and BY WHOM (operator spec
        # 2026-08-01: the scanned QR should tell the whole story).
        import time as _t
        cert.setdefault("born", _t.strftime("%Y-%m-%d %H:%M"))
        try:
            from provisioning import tool_identity
            byline = tool_identity.tool_name() or "Node Medic"
            uh = (tool_identity.identity_hash() or "")[:8]
            cert.setdefault("built_by", byline + (f" ({uh})" if uh else ""))
        except Exception:
            pass
        try:
            self._saved_cert_id = save_cert(cert)     # keep it on the medic
            cert["_id"] = self._saved_cert_id
        except OSError:
            self._saved_cert_id = None
        self._cert = cert
        self._register_kin(cert)                  # stamp builder=this medic's unit
        # This position has been consumed by THIS node — never let it ride onto
        # the next birth (2026-08-01 bug hunt).
        self._prefill_location = None
        self.list.add_widget(_line("Birth certificate:", bold=True, size="16sp"))
        self.list.add_widget(_line("    (saved on this Node Medic)",
                                   size="12sp", color="text_secondary"))
        for k, v in cert.items():
            if k.startswith("_"):
                continue
            self.list.add_widget(_line(f"    {k}: {v}", size="13sp"))
        self._add_cert_qr(cert)
        self._add_notes_panel()
        # The ceremony's closing act: a deliberate DONE under the certificate
        # (all births) — auto-home raced past the cert + QR (operator spec
        # 2026-08-01).
        done = Button(text="Done — back to home", size_hint_y=None,
                      height=dp(56), bold=True, font_size="17sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _done(*_a):
            try:
                self._exit_flash_view()    # next BIRTH visit starts fresh
                from kivy.app import App
                app = App.get_running_app()
                if app is not None and hasattr(app, "switch_mode"):
                    app.switch_mode("home")
            except Exception:
                pass
        done.bind(on_release=_done)
        self.list.add_widget(done)
        try:
            from kivy.clock import Clock
            Clock.schedule_once(lambda dt: self._scroll_to_cert(), 0.2)
        except Exception:
            pass

    def _scroll_to_cert(self):
        """Bring the certificate (QR) into view — best-effort."""
        try:
            for w in getattr(self, "_qr_widgets", []) or []:
                if w.parent:
                    self.scroll.scroll_to(w, padding=dp(30))
                    return
            self.scroll.scroll_y = 0       # cert lives at the bottom
        except Exception:
            pass

    def _register_kin(self, cert):
        """Record the birthed node in the medic's kin roster, stamped with
        builder = THIS medic's own unit hash — so it shows as kin, and drops to
        neighbour on VITALS/SCAN if this unit's trust is ever revoked. Best-effort."""
        try:
            from monitor import kin_roster
            from provisioning import tool_identity
            # Prefer the rtnode.health destination (the registry key its beacon
            # announces from) so a propagation node shows up NAMED, not as an
            # anonymous neighbour; fall back to the main identity for node types
            # that key on it.
            h = (cert.get("health_dst") or cert.get("reticulum_address")
                 or cert.get("identity_hash"))
            if not h:
                return
            # Coordinates come from the CERT — which the confirm-location gate
            # owns (moved pin -> corrected; cancelled -> removed). Reading
            # _prefill_location here bypassed that gate entirely: a rejected
            # pin still landed on the SCAN map (2026-08-01 bug hunt).
            lat = lon = None
            from ui.screens.cert_view_screen import cert_latlon
            ll = cert_latlon(cert)
            if ll:
                lat, lon = ll[0], ll[1]
            kin_roster.register(
                h, cert.get("node_name") or cert.get("hostname") or "node",
                node_type=cert.get("type", "rtnode2400"), lat=lat, lon=lon,
                builder=tool_identity.identity_hash())
        except Exception as e:
            print(f"[kin] register skipped: {e}")

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
