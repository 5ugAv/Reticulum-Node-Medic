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

import re
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
from ui.i18n import tr  # i18n: wrapped — screen chrome/buttons/popups; the birth
                        # certificate PAYLOAD (fields, radio-proof verdicts) is a
                        # DOCUMENT and stays in English
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
    ("pi_rnode", "Raspberry Pi propagation node  (its radio is optional)"),
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

# MITOSIS_BOARDS lived here and was removed 2026-08-07 (task #57). It read
# {"heltec_wireless_tracker"} and restricted cloning to the one board whose
# GPS path we had proven, "so a clone's location function is guaranteed
# correct". Nothing ever referenced it — and the reason is that the design
# moved underneath it: MITOSIS now clones this medic onto a fresh PI over the
# wire (ui/app.py _mitosis_factory), so there is no board for it to restrict.
#
# THE INTENT IS STILL LIVE, just relocated: a Pi has no GPS of its own, so a
# clone's location comes from a Heltec Tracker plugged into IT
# ([[medic-gps-via-tracker]]). Whoever wires the real clone flow should carry
# the rule forward there — only bless a GPS board proven end-to-end — rather
# than rediscover it.

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
    "install_health_reporter": 25, "install_status_server": 15,
    "apply_system_hardening": 10, "set_hostname": 5, "final_verification": 15,
    # An rnpath wait plus a beacon wait; on the Pi path it is only the HTTP
    # poll and returns in seconds, but the bar must not stall on the slow case.
    "prove_the_node_reports": 45,
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
    "install_status_server": "Giving the node a status page Node Medic can read…",
    "prove_the_node_reports": "Asking the node to report on itself…",
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
    return ["detect_port", "ensure_single_board", "ensure_firmware", "flash",
            "set_params", "verify"]      # 'verify' was missing -> ring hit 100% early


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


#: Failures that come from the SETUP step, not from flashing the board. The
#: flash already succeeded when these fire, so board-recovery advice (hold
#: BOOT, tap RST, change the cable) is advice for a fault the operator does
#: not have. See workflows/rtnode_build.wifi_onboarding's failure branches.
_ONBOARDING_FAILURE = re.compile(
    r"wifi_onboarding|isn't on WiFi|couldn't read the WiFi|5 GHz|"
    r"configure the node manually|RTNode-Setup",
    re.I)


def _line(text, color="text_primary", bold=False, size="15sp"):
    # height follows the wrapped text — fixed heights made long lines overlap
    lbl = Label(text=text, halign="left", valign="middle", bold=bold,
                font_size=theme.font_sp(size),
                color=theme.hex_to_rgba(theme.COLORS[color]),
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
                 on_mitosis=None, prefill_location=None, on_guide=None,
                 on_salvage=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
        # (lat, lon, source) stamped from the map's "Use this position", or None.
        self._prefill_location = prefill_location
        # Whether THIS birth publishes a position to the public mesh map. Only
        # the guided walkthrough's own share screen sets it (begin_guided); a
        # birth started any other way stays hidden, because nobody was asked.
        self._share_location = "hidden"
        # on_guide() — open the step-by-step guided birth (for a new operator).
        self._on_guide = on_guide
        # on_salvage() — open "Show me what you got", for a keeper whose
        # hardware is not one of the boards this screen knows how to offer.
        self._on_salvage = on_salvage
        # When arriving from the guide with a chosen kind, don't let auto-detect
        # flip the firmware family out from under the operator (cleared on a manual
        # firmware tap). None = detection decides (the 'radio' path).
        self._forced_firmware = None
        self._saved_cert_id = None
        # Step one is naming the NEW node. Created once and re-parented on each header
        # rebuild so a typed name survives board changes. (Existing nodes are reached
        # from VITALS/SCAN -> their certificate card, which offers Triage.) Notes are
        # asked at the END (after the cert).
        self._name_in = TextInput(hint_text=tr("Name this node  (e.g. Rooftop-East)"),
                                  multiline=False, size_hint_y=None, height=dp(46),
                                  font_size="26sp")
        self._end_notes_in = TextInput(
            hint_text=tr("Notes  (optional — mast height, landmarks…)"),
            multiline=True, size_hint_y=None, height=dp(70), font_size="20sp")
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
        # True unless a guided hand-off says this radio was never plugged into
        # the medic at all (2026-09-06, live: operator's Pi already carried a
        # working RAK4631, on its own power, never on the medic's USB — and
        # "Board (radio)" still blocked the build asking them to identify it).
        # Load-bearing only when the medic is about to FLASH the radio, which
        # decides the firmware image; a Pi build using an already-flashed
        # radio needs no image decision at all.
        self._flash_radio = True
        self._sel_pi = None                          # chosen (key, name) | None
        self._firmware = None                        # rtnode2400 | rnode | pi_rnode
        self._rtnode_target = None                   # RTNODE_TARGETS key (RTNode-2400)
        self._detected = None                        # last board_detect result
        self._detecting = False
        self._declared_board_key = None              # board the operator confirmed
        self._declared_mismatch = ""                 # ...and how the silicon disagrees
        self._declared_pi_address = ""               # link the walkthrough proved

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
        # Mark content below the fold. A green start button under the fold reads
        # as an ABSENT button, so the screen looks broken (operator, 2026-08-02).
        try:
            from ui.widgets.scroll_hint import attach as _attach_hint
            _attach_hint(self.scroll, parent=self)
        except Exception:
            pass

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
        arrived = getattr(self, "_from_imaging", None)
        if arrived:
            named = arrived if isinstance(arrived, str) else tr("this node")
            self.header.add_widget(_line(
                tr("Card written — now building {name}").format(name=named),
                bold=True, size="22sp",
                color="green"))
            self.header.add_widget(_line(tr(
                "Everything below is already filled in from the card you just "
                "wrote. This is the last step, not the start again."),
                size="13.5sp", color="text_secondary"))
        elif getattr(self, "_declared_pi_address", ""):
            # ARRIVED FROM THE END OF A WALKTHROUGH, not from the start of one.
            # The radio is flashed, the card is written, the Pi is up on the
            # cable — this screen's only remaining job is to install the mesh
            # software and issue the certificate. Headed "Birth a new node"
            # above an editable "Name this node" field, it reads as starting
            # over, and the operator said so the moment they saw it (2026-08-09:
            # "i pressed wake it up and got taken to this name screen, the node
            # has already been named"). Nothing WAS being re-asked; the heading
            # was simply describing the wrong thing.
            nm = (getattr(self, "_name_in", None) is not None
                  and self._name_in.text.strip()) or tr("this node")
            self.header.add_widget(_line(
                tr("Bring {name} to life").format(name=nm), bold=True,
                size="22sp"))
            self.header.add_widget(_line(tr(
                "The radio is flashed, the card is written and the Pi is "
                "answering on the cable. This is the last part: the mesh "
                "software, and its birth certificate."),
                size="13.5sp", color="text_secondary"))
        else:
            self.header.add_widget(_line(tr("Birth a new node"), bold=True,
                                         size="22sp"))

        # The step-by-step guide entry lives at the BOTTOM as a modest link —
        # a big green button at the top read as 'continue' and yanked operators
        # back to the guide's start mid-birth (2026-07-30 report).

        # Step one: name the NEW node being built. (Existing nodes live in VITALS /
        # SCAN — tap one to open its certificate, which offers Triage.)
        #
        # Past the end of a walkthrough the node HAS a name — one the operator
        # chose several screens ago — so asking for it again is the wrong label
        # on a field that is merely still editable.
        self.header.add_widget(_line(
            tr("Its name") if getattr(self, "_declared_pi_address", "")
            else tr("Name this node"), bold=True, size="15sp", color="accent"))
        self.header.add_widget(self._name_in)
        if self._prefill_location:
            lat, lon, src = self._prefill_location
            self.header.add_widget(_line(
                tr("Location stamped: {lat}, {lon}  (from {src})").format(
                    lat=f"{lat:.5f}", lon=f"{lon:.5f}", src=src),
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
                self.header.add_widget(_line(tr("Choose your hardware:"),
                                             size="13sp",
                                             color="text_secondary"))
                detect = Button(
                    text=tr("Detecting board…") if self._detecting
                    else tr("Detect connected board"),
                    size_hint_y=None, height=dp(48), bold=True, disabled=self._detecting,
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
                detect.bind(on_release=lambda *_: self._detect_board())
                self.header.add_widget(detect)
                # THE OTHER DOOR. "Detect connected board" only answers for
                # someone holding one of the 16 boards the medic knows. The
                # operator's brief (2026-09-03) is remote communities using
                # "any hardware people might have lying around" — an old
                # handheld radio, a scrap board with no radio on it, a phone.
                # That keeper needs a way in that does not start by assuming
                # they bought the right thing.
                salvage = Button(
                    text=tr("Not one of these?  Show me what you got"),
                    size_hint_y=None, height=dp(44), font_size="14sp",
                    background_normal="", background_down="",
                    background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                    color=theme.hex_to_rgba(theme.COLORS["accent"]))
                salvage.bind(on_release=lambda *_: self._open_salvage())
                self.header.add_widget(salvage)
            if self._detected is not None:
                found = self._detected.get("found")
                self.header.add_widget(_line(self._detect_summary(), size="12.5sp",
                                             color="green" if found else "amber"))
                # A board that had to be WAITED for is rebooting in a loop. Say
                # that here rather than let the operator read a clean green
                # "Detected ESP32-S3" and wonder why the flash then fought them.
                if self._detected.get("unstable"):
                    self.header.add_widget(_line(
                        self._detected.get("unstable_reason", ""),
                        size="12.5sp", color="amber"))
            det = self._detected or {}
            det_opts = det.get("firmware") if det.get("found") else None
            # ANY RNode-capable board can equally be a Pi's radio, so that is a
            # second option and the operator has to be ASKED which they want.
            # This used to live inside the `len > 1` branch below, which made it
            # unreachable for precisely the boards that needed it: a RAK4631
            # detects as firmware ['rnode'] — length ONE — so the branch was
            # skipped, pi_rnode was never added, and the screen presented "RNode"
            # as already decided with only a small "change" link. Rebirthing a
            # board therefore led straight back to what it had been (operator,
            # 2026-08-06: "it just goes straight back to flashing what the board
            # previously was").
            if det_opts and "rnode" in det_opts and "pi_rnode" not in det_opts:
                det_opts = list(det_opts) + ["pi_rnode"]
            if det_opts and len(det_opts) > 1:
                # Detection NARROWED but couldn't decide — ask with the full
                # labels so the operator picks the family deliberately
                # (auto-leading with RTNode-2400 built the wrong firmware —
                # Tern1 incident, 2026-07-31).
                self.header.add_widget(_line(
                    tr("What should this board become?"), bold=True, size="15sp",
                    color="accent"))
                # Uniform family order EVERYWHERE (operator spec 2026-07-31):
                # RNode -> RTNode-2400 -> Pi + RNode.
                _rank = {"rnode": 0, "rtnode2400": 1, "pi_rnode": 2}
                det_opts = sorted(det_opts, key=lambda k: _rank.get(k, 99))
                for key in det_opts:
                    b = Button(text=tr(FIRMWARE_LABEL.get(key, key)),
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
                self.header.add_widget(_line(tr("Firmware"), bold=True, size="15sp",
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
            if self._declared_mismatch:
                self.header.add_widget(_line(self._declared_mismatch,
                                             size="14sp", color="amber"))
            if not getattr(self, "_flash_radio", True):
                # The operator said so explicitly, earlier in the guide: this
                # radio was never on the medic's USB and never will be — there
                # is no image to pick and nothing here to identify. Since
                # 2026-09-22 the guide asks which board it is, so the name is
                # shown (and can be changed) — but this row must never read
                # like the flash road's: the instruction to plug the radio
                # into the Pi afterwards is the one thing this road needs.
                if self._sel_board is not None:
                    self.header.add_widget(self._labelled_row(
                        tr("Board (radio)"),
                        self._sel_button(self._sel_board.display_name,
                                         self._choose_board)))
                else:
                    self.header.add_widget(_line(tr("Board (radio)"), bold=True,
                                                 size="15sp", color="accent"))
                self.header.add_widget(_line(tr(
                    "Not flashed here — already working. Plug it into the "
                    "Pi when this finishes."), size="13.5sp",
                    color="text_secondary"))
            elif self._sel_board is None:
                self.header.add_widget(_line(tr("Board (radio)"), bold=True,
                                             size="15sp", color="accent"))
                self._add_rnode_board_pick()
            elif self._declared_board_key:
                # ONE choice, ONE confirmation (operator's rule, 2026-08-09).
                # The board was picked in the guide and confirmed there against
                # its photograph. Re-showing the photo under "check it matches"
                # is the same question a third time, and a question asked when
                # the answer is already known trains people to tap past it.
                self.header.add_widget(self._labelled_row(
                    tr("Board (radio)"),
                    self._sel_button(self._sel_board.display_name,
                                     self._choose_board)))
                self.header.add_widget(_line(
                    tr("✓ Confirmed earlier — tap to change it."),
                    size="12.5sp", color="green"))
            else:
                # Show the PHOTO of what was auto-picked. The medic identifies
                # the board from silicon the operator can't see, so a name in
                # text is a claim they have no way to check — with the picture
                # they can hold the board up against it (operator, 2026-08-02).
                try:
                    from ui import board_images
                    from ui.widgets.board_card import BoardCard
                    if board_images.image_for(self._sel_board.key):
                        # THE NODE'S name on the OLED, not the board's, and
                        # LIVE (briefing Task 8): the preview shows what the
                        # firmware will actually draw, as it is typed.
                        card = BoardCard(self._sel_board.key,
                                         name=(self._name_in.text or "").strip().upper(),
                                         on_select=lambda *_a: self._choose_board(),
                                         selected=True,
                                         size_hint_y=None, height=dp(150))
                        self.header.add_widget(card)
                        self._name_in.bind(text=lambda _i, t, c=card:
                                           c.set_name(t))
                except Exception:
                    pass
                self.header.add_widget(self._labelled_row(
                    tr("Board (radio)"),
                    self._sel_button(self._sel_board.display_name,
                                     self._choose_board)))
                # Say that this one was FOUND. Next to it sits the Host Pi row,
                # which asks to be tapped — and with both rendered as identical
                # grey buttons an operator reads the pair as "it's asking me to
                # choose a board" even though the board is already known
                # (walkthrough 2026-08-02).
                self.header.add_widget(_line(tr(
                    "✓ Found by Node Medic — check it matches the board in your "
                    "hand. Tap to change it."), size="12.5sp", color="green"))
                # For a board with near-identical siblings, name the mark that
                # actually differs — a photo alone invites a false confirmation.
                try:
                    from ui import board_images as _bi
                    tell = _bi.how_to_tell(self._sel_board.key)
                    if tell:
                        self.header.add_widget(_line(tell, size="12.5sp",
                                                     color="accent"))
                except Exception:
                    pass
            if self._firmware == "pi_rnode":
                self.header.add_widget(Widget(size_hint_y=None, height=dp(10)))

                # Identify the Pi rather than asking. It is the input the power
                # check reasons about, so a wrong pick produces a wrong verdict
                # about needing a powered hub (operator asked for this mid-
                # walkthrough, 2026-08-02). Auto-select ONLY on an exact read
                # from the Pi itself — the boot-ROM chip id cannot tell a Zero
                # 2 W from a 3 A+, and that is a 500 mA vs 1000 mA difference.
                detected_note = ""
                if self._sel_pi is None:
                    try:
                        from provisioning import pi_model
                        host = pi_model.detect()
                        names = dict(PI_HOSTS)
                        if host.should_auto_select and host.key in names:
                            self._sel_pi = (host.key, names[host.key])
                        _host_assumed = host.is_assumed
                        detected_note = pi_model.describe(
                            host, lambda k: names.get(k, k))
                    except Exception:
                        detected_note = ""
                self.header.add_widget(self._labelled_row(
                    tr("Host Pi"),
                    self._sel_button(
                        tr(self._sel_pi[1]) if self._sel_pi
                        else tr("Tap to choose which Raspberry Pi"),
                        self._choose_pi)))
                if detected_note:
                    # Green only when the Pi actually told us. An assumption
                    # gets amber, so a filled-in field is never mistaken for a
                    # confirmed one (operator asked why it couldn't detect the
                    # Pi, 2026-08-02 — the answer is that it can't YET).
                    assumed = bool(locals().get("_host_assumed"))
                    self.header.add_widget(_line(
                        detected_note, size="12.5sp",
                        color="amber" if assumed else "green"))
                # The medic NAMED the Pi when it imaged the card, so it can
                # offer the address itself — an operator has no way to know an
                # IP, and being unable to continue without one blocked the
                # whole path (operator report 2026-08-01).
                # An address the WALKTHROUGH already proved wins outright: its
                # gate would not have opened until the node answered on it, so
                # it is measured rather than suggested. suggested_address()
                # re-runs the same discovery on the UI thread, which is a stall
                # of up to a minute for an answer we are already holding.
                suggestion = getattr(self, "_declared_pi_address", "") or ""
                if not suggestion:
                    try:
                        from provisioning.pi_discover import suggested_address
                        suggestion = suggested_address()
                    except Exception:
                        pass
                # BEFORE asking for an address: does this Pi even have an
                # operating system yet? A Pi in boot-ROM mode (blank card) has
                # no network and CANNOT have an address, so asking for one is an
                # unanswerable question — and pressing Start produced "Pi
                # address needed", which is true and useless (operator hit this
                # twice, 2026-08-02). Route to imaging instead.
                if self._pi_needs_imaging():
                    # Power-compatibility FIRST. It used to be checked at the
                    # build button — after naming, after imaging, after a
                    # four-minute card write. A blocked pairing changes WHICH
                    # BOARD you use, so it has to arrive before the operator
                    # spends anything on the plan (operator, 2026-08-02:
                    # "should I be warned already about the incompatibility of
                    # the board I'm about to use").
                    blocked = self._power_banner()
                    if blocked:
                        # Do NOT offer to write a card for a node that cannot
                        # work. The next useful action is finding hardware that
                        # does, so THAT is the button; leaving "Set up this Pi's
                        # card" as the main action invites the operator to spend
                        # four minutes building the thing just refused
                        # (operator, 2026-08-02).
                        # A powered hub in the room changes the answer — but
                        # only if it goes WITH the node, which is the one thing
                        # the medic cannot see. So it is offered as a question
                        # (operator, 2026-08-02).
                        if self._offer_hub_route():
                            return
                        self.header.add_widget(_line(
                            tr("Options"), bold=True, size="17sp", color="accent"))
                        self._recommend_button()
                        # The hub is the WORKAROUND, not the advice — so it sits
                        # below the good answer and reads smaller.
                        self.header.add_widget(_line(tr(
                            "Or use a powered USB hub between the Pi and the "
                            "board to get around this."), size="12.5sp",
                            color="text_secondary"))
                        self._back_home_button()
                        return
                    # Boot-ROM evidence proves only "started with nothing
                    # to boot" — presence and blankness of a card were never
                    # read (the imager's twin claim fell 2026-09-13; this one
                    # hid untranslated until the wrap found it, 2026-09-15).
                    self.header.add_widget(_line(tr(
                        "This Raspberry Pi started up with nothing to boot "
                        "— no working card seen."), size="13.5sp",
                        color="amber"))
                    self.header.add_widget(_line(tr(
                        "Node Medic will write its card first, then reach it "
                        "over the same cable. There's no address to enter."),
                        size="13sp", color="text_secondary"))
                    go = Button(text=tr("Set up this Pi's card  →"),
                                size_hint_y=None,
                                height=dp(54), bold=True, font_size="16sp",
                                background_normal="",
                                background_color=theme.hex_to_rgba(
                                    theme.COLORS["accent"]),
                                color=theme.hex_to_rgba(
                                    theme.COLORS["background"]))
                    go.bind(on_release=lambda *_: self._go_image_pi())
                    self.header.add_widget(go)
                    return
                # AN ADDRESS THE WALKTHROUGH JUST PROVED IS NOT A GUESS.
                # The guided step before this one does not open until the node
                # answers, so by the time this screen draws, the medic has
                # already spoken to the Pi. Running the general search again
                # anyway meant a slower, weaker probe got the last word: it
                # printed "Couldn't find the Pi yet — is it powered on?" in
                # amber, directly above a Start button that worked perfectly
                # (operator, 2026-08-10). A false alarm next to a working
                # button teaches the operator to distrust the warnings that
                # matter. So proof wins, and the only thing re-checked is that
                # same address — never a sweep that can disagree about a
                # different machine.
                settled = False
                proved = getattr(self, "_declared_pi_address", "") or ""
                if proved:
                    if not hasattr(self, "_pi_addr_in"):
                        from ui.onscreen_keyboard import bind_field
                        from kivy.uix.textinput import TextInput
                        self._pi_addr_in = bind_field(TextInput(
                            text="", multiline=False, font_size="27sp"))
                        self._pi_user_in = bind_field(TextInput(
                            text="pi", multiline=False, font_size="27sp"))
                    self._pi_addr_in.text = proved
                    self.header.add_widget(_line(
                        tr("Raspberry Pi answered at {addr} a moment ago — "
                           "change it below if that is no longer where it "
                           "is.").format(addr=proved),
                        size="13.5sp", color="green", bold=True))
                    # THE FIELD STAYS, AND THAT IS THE WHOLE POINT.
                    #
                    # This branch was added the same day to stop a slower probe
                    # contradicting an address the walkthrough had just proved.
                    # It went too far: it removed the field entirely, so a proof
                    # that had since gone STALE could not be corrected. It duly
                    # did — SkyFinger's cable link wedged, the node moved to
                    # Wi-Fi, and the screen sat insisting on 10.55.0.1 with
                    # nowhere to type (2026-08-11).
                    #
                    # A proof from five minutes ago is evidence, not a fact
                    # about now. Lead with it, and let the operator overrule it.
                    settled = "prefilled"
                # If the Pi is plugged into the medic, there is nothing to ask.
                # Showing an address box for a device physically in front of the
                # operator is the thing they objected to in the first place
                # (2026-08-01) — so say what we found and move on.
                cable = ""
                if not settled:            # already answered; don't stall 3s again
                    try:
                        from provisioning.pi_discover import cable_address
                        cable = cable_address(timeout=3.0)
                    except Exception:
                        cable = ""
                if cable:
                    self.header.add_widget(_line(
                        tr("Raspberry Pi connected by cable — nothing to enter."),
                        size="13.5sp", color="green", bold=True))
                    if not hasattr(self, "_pi_addr_in"):
                        from ui.onscreen_keyboard import bind_field
                        from kivy.uix.textinput import TextInput
                        self._pi_addr_in = bind_field(TextInput(
                            text="", multiline=False, font_size="27sp"))
                        self._pi_user_in = bind_field(TextInput(
                            text="pi", multiline=False, font_size="27sp"))
                    self._pi_addr_in.text = cable
                    settled = True   # nothing to ask; fall through to the Build button
                # ONLY WHEN THE ADDRESS IS STILL AN OPEN QUESTION.
                # Both branches above used to `return` here, which skipped
                # the end of this method — and the end of this method is
                # where the Build button is drawn. So the screen that had
                # just proved it could reach the Pi was the one screen with
                # nothing to press (operator, 2026-08-10: "this page needs
                # to direct the user what to do next?").
                if settled != True:
                    self.header.add_widget(_line(
                        # There is no Find button — it was removed on 2026-08-02
                        # because the medic searches by itself. The copy telling
                        # the operator to tap it outlived it by a week, pointing at
                        # a control that is not on the screen (operator, 2026-08-10).
                        (tr("Where the Pi is on your network — filled in from the "
                            "name Node Medic gave it. Type over it if it's wrong.")
                         if suggestion else
                         tr("Where the Pi is on your network. Node Medic is "
                            "looking for it — or type the address in.")),
                        size="12.5sp", color="text_secondary"))
                    row = BoxLayout(orientation="horizontal", size_hint_y=None,
                                    height=dp(48), spacing=dp(8))
                    if not hasattr(self, "_pi_addr_in"):
                        from ui.onscreen_keyboard import bind_field
                        from kivy.uix.textinput import TextInput
                        self._pi_addr_in = bind_field(TextInput(
                            text="", multiline=False, font_size="27sp",
                            hint_text=tr("found automatically — or type it")))
                        self._pi_user_in = bind_field(TextInput(
                            text="pi", multiline=False, font_size="27sp",
                            hint_text=tr("user"), size_hint_x=0.22))
                    if suggestion and not self._pi_addr_in.text.strip():
                        self._pi_addr_in.text = suggestion
                    for w_ in (self._pi_addr_in, self._pi_user_in):
                        if w_.parent is not None:
                            w_.parent.remove_widget(w_)
                    row.add_widget(self._pi_addr_in)
                    row.add_widget(self._pi_user_in)
                    # No Find button. The medic looks by itself — over the cable
                    # first, then the name it gave the Pi — so a button asking the
                    # operator to trigger a search is asking them to do the tool's
                    # job (operator, 2026-08-02). It searches on open instead.
                    # ALWAYS, even over a prefilled proof: it is the check
                    # that turns a five-minute-old address into a current one,
                    # and it only ever REPLACES the field with somewhere that
                    # actually answered.
                    from kivy.clock import Clock as _Clock
                    _Clock.schedule_once(lambda _dt: self._find_pi(), 0.4)
                    self.header.add_widget(row)
                    self._pi_find_status = _line("", size="12sp", color="green")
                    self.header.add_widget(self._pi_find_status)

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
        self.header.add_widget(_line(tr(
            "The board will restart itself during the flash — its screen and "
            "LED may blink, and it may vanish from USB for a few seconds. "
            "That's normal. Keep it plugged in."),
            size="13sp", color="text_secondary"))

    def _exit_flash_view(self, keep_list=False):
        if getattr(self, "_flash_view", False):
            self._flash_view = False
            self._build_chooser(keep_list=keep_list)

    def handle_back(self):
        """Left-edge swipe off BIRTH. Always returns False — BIRTH is a single
        page, so the swipe goes home either way — but the finished build's view
        is dropped on the way out.

        Without this, swiping home from the certificate instead of tapping
        'Done' left ``_flash_view`` True forever: the two places that cleared it
        were the failure popup's on_dismiss and the Done button, and the swipe
        is a first-class exit that reached neither. The next visit to BIRTH then
        opened on the previous build's page (audit, 2026-08-03).

        A RUNNING build keeps its page: the operator may swipe home to watch the
        banner and come back, and they should return to the progress they left,
        not a chooser."""
        try:
            from kivy.app import App
            app = App.get_running_app()
            running = bool(app is not None and app.flash_in_progress())
        except Exception:
            running = False
        if not running:
            self._exit_flash_view()
        return False

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
                self.list.add_widget(_line(tr(
                    "Flashes the attached board with RTNode-2400 and provisions it "
                    "on the standard channel. WiFi/LoRa details are entered on the "
                    "node's setup portal after flashing."), size="12.5sp",
                    color="text_secondary"))
            else:
                self.list.add_widget(_line(tr("Choose the RTNode-2400 target above."),
                                           size="13sp", color="text_secondary"))
        elif self._firmware in ("rnode", "pi_rnode"):
            radio_known_or_moot = (self._sel_board is not None
                                   or (self._firmware == "pi_rnode"
                                       and not getattr(self, "_flash_radio", True)))
            if radio_known_or_moot:
                # board=None here means "deliberately not identified" — the
                # standard network params (frequency/BW/SF/CR/power) apply
                # uniformly; they are not board-specific, and this radio's own
                # firmware already carries whatever it was set at birth.
                self.show_params(self._firmware, board=self._sel_board)
            else:
                self.list.add_widget(_line(tr("Pick a board above to set radio "
                                              "params and start."), size="13sp",
                                           color="text_secondary"))
        else:
            self.list.add_widget(_line(
                tr("Detect the connected board, or choose firmware, to begin."),
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
        cancel = Button(text=tr("Cancel"), size_hint_y=None, height=dp(48), bold=True,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        cancel.bind(on_release=lambda *_: popup.dismiss())
        root.add_widget(cancel)
        popup.content = root
        popup.open()

    def _open_salvage(self):
        """Hand over to the salvage screen. Best-effort: a link that fails must
        not take the birth screen down with it."""
        try:
            if self._on_salvage:
                self._on_salvage()
        except Exception:
            pass

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
                num, tag = next_custom, "  " + tr("(custom)")
                next_custom += 1
            # picker_label, not display_name: it carries the silkscreen marking
            # for boards sold under a name that is printed nowhere on them
            # (LoRa32 v2.1 = T3 v1.6.1) — the row IS the moment of choice.
            entries.append((num, f"{board.picker_label}{tag}",
                            lambda b=board: self._confirm_rnode_board_gate(b)))
        self._picker_popup(tr("Select the board"), entries)

    def _pick_board(self, board):
        self._sel_board = board
        # Picked HERE, through this screen's own confirm gate — so this is now
        # the one choice and the one confirmation, and whatever the guide was
        # told earlier no longer stands (nor does its disagreement warning).
        self._declared_board_key = None
        self._declared_mismatch = ""
        self._build_chooser()

    def _pi_candidates(self):
        """Every address this node could answer on, best first.

        THE OPERATOR SHOULD NEVER TYPE AN ADDRESS. They named the node; the
        medic gave that name to the card, so it already knows what the node
        calls itself on the network. Asking a person to translate their own
        node's name into a hostname is asking them to do the tool's arithmetic
        (operator, 2026-08-11: "they have to type a name and the rest is done
        for them — maybe Node Medic can take the name and add .local").

        Ordered, because the answers age differently:
          1. the address the walkthrough actually PROVED, if there is one;
          2. <name>.local — derived from the name they typed, which is exactly
             what the card was written with;
          3. the last Pi this medic imaged, for a lap that lost its name.
        """
        out = []
        proved = getattr(self, "_declared_pi_address", "") or ""
        if proved:
            out.append(proved)
        try:
            from provisioning.pi_imager import hostnameify
            nm = hostnameify((self._name_in.text if hasattr(self, "_name_in")
                              else "") or self._node_name_hint())
            if nm:
                out.append(f"{nm}.local")
        except Exception:                                      # noqa: BLE001
            pass
        try:
            from provisioning.pi_discover import suggested_address
            sa = suggested_address()
            if sa:
                out.append(sa)
        except Exception:                                      # noqa: BLE001
            pass
        from provisioning.pi_discover import addresses_for
        seen, ordered = set(), []
        for a in out:
            for one in addresses_for(a):
                if one and one not in seen:
                    seen.add(one)
                    ordered.append(one)
        return ordered

    def _node_name_hint(self):
        return (getattr(self, "_declared_name", "")
                or getattr(self, "_node_name", "") or "")

    def _find_pi(self):
        """Try every address the node could answer on, and keep the one that does.

        Was: ask pi_discover, take what it says. Now it walks _pi_candidates in
        order and stops at the first that opens SSH — because the address that
        was true five minutes ago may not be true now. SkyFinger proved that the
        hard way: its cable link wedged mid-build, the node was perfectly
        reachable on Wi-Fi, and the screen went on insisting on 10.55.0.1
        (2026-08-11). Runs off-thread; probes are a TCP connect, not a sweep.
        """
        status = getattr(self, "_pi_find_status", None)
        if status is not None:
            status.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
            status.text = tr("Looking for the Pi…")
        import threading

        def work():
            res = {}
            try:
                from provisioning.link import _port_open
                for addr in self._pi_candidates():
                    if _port_open(addr, 22, timeout=4.0):
                        # KEEP THE IP IT ANSWERED ON, NOT THE NAME.
                        #
                        # A node with a cable AND Wi-Fi advertises the same
                        # mDNS name on both, and <name>.local resolves to
                        # whichever it announced most recently. skyfinger.local
                        # answered here on Wi-Fi, and by the time the build
                        # asked, mDNS handed it the cable — which was dead
                        # (2026-08-11). The probe and the build were talking
                        # about different machines under one name.
                        #
                        # A name that can resolve two ways is not an address.
                        # Pin the one that actually answered.
                        res = {"address": addr, "ip": addr,
                               "confirmed": True, "how": tr("it answered there")}
                        break
                if not res:
                    from provisioning.pi_discover import find_pi
                    res = find_pi(self._pi_addr_in.text.strip())
            except Exception as e:            # noqa: BLE001
                res = {"how": tr("couldn't search: {err}").format(
                    err=str(e)[:60])}
            Clock.schedule_once(lambda _dt: self._found_pi(res), 0)
        threading.Thread(target=work, daemon=True).start()

    def _found_pi(self, res):
        status = getattr(self, "_pi_find_status", None)
        addr = (res or {}).get("address") or ""
        # Only fill the field from an answer tied to THIS build — the cable, or
        # the name the medic itself gave the Pi. A network sweep proves a Pi
        # exists, never that it is ours: on 2026-08-02 it offered an unrelated
        # Pi on the operator's LAN while the real target sat on USB with no
        # address at all. The build rewrites the target's services, so filling
        # that in automatically is the most destructive thing this screen does.
        if addr and not (res or {}).get("confirmed", True):
            addr = ""
        if addr:
            self._pi_addr_in.text = addr
            if status is not None:
                status.color = theme.hex_to_rgba(theme.COLORS["green"])
                ip = res.get("ip", "")
                status.text = (tr("Found the Pi at {ip} — {how}.").format(
                                   ip=ip, how=res.get("how", ""))
                               if ip else
                               tr("Found it — {how}.").format(
                                   how=res.get("how", "")))
            return
        if status is not None:
            status.color = theme.hex_to_rgba(theme.COLORS["amber"])
            how = (res or {}).get("how")
            cands = (res or {}).get("candidates")
            status.text = (
                f"{how}: {cands}" if cands else
                (how or tr("Couldn't find the Pi yet — is it powered on and "
                           "joined to your WiFi? It can take a minute or two.")))

    def _add_rnode_board_pick(self):
        """The RNode board pick, RTNode-style: detection narrows the catalogue
        to boards matching the read chip and OFFERS them right here — photo
        cards where we have photos, wide buttons otherwise (operator spec
        2026-07-31: the old blank 'tap to choose' hid the choice in a popup).
        No detection -> the full-list picker button, as before."""
        det = self._detected or {}
        shortlist = det.get("boards") or []
        if not det.get("found") or not shortlist:
            self.header.add_widget(self._sel_button(tr("Tap to choose a board"),
                                                    self._choose_board))
            return
        self.header.add_widget(_line(
            tr("Detected {chip} — which board is this?").format(
                chip=det.get("platform") or det.get("chip")),
            size="13sp", color="text_secondary"))
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
                            height=dp(120) + dp(26), spacing=dp(10))
            for b in with_photo[i:i + 3]:
                # Photo AND words. A stranger cannot pick a board from a
                # picture alone — the friend's Heltec V4, 2026-09-21: "there
                # was no words until the actual board is selected".
                col = BoxLayout(orientation="vertical", spacing=dp(2))
                col.add_widget(BoardCard(
                    b.key, name=self._name_in.text.strip(),
                    on_select=lambda bb=b: self._confirm_rnode_board_gate(bb)))
                cap = _line(b.display_name, bold=True, size="14sp",
                            color="text_primary")
                cap.halign = "center"
                cap.size_hint_y = None
                cap.height = dp(24)
                col.add_widget(cap)
                row.add_widget(col)
            for _ in range(3 - len(with_photo[i:i + 3])):
                row.add_widget(Widget())     # keep card widths consistent
            self.header.add_widget(row)
        for b in shortlist:
            if b in with_photo:
                continue
            self.header.add_widget(self._sel_button(
                b.display_name,
                lambda bb=b: self._confirm_rnode_board_gate(bb)))
        other = self._sel_button(tr("Not one of these — full board list"),
                                 self._choose_board)
        other.height = dp(40)
        other.font_size = "13sp"
        other.color = theme.hex_to_rgba(theme.COLORS["text_secondary"])
        self.header.add_widget(other)

    def _choose_pi(self):
        """Full-screen picker of host Pis — plus 'None' for a standalone radio."""
        entries = [(i, tr(name), lambda k=key, n=name: self._pick_pi(k, n))
                   for i, (key, name) in enumerate(PI_HOSTS, 1)]
        self._picker_popup(tr("Select the host Pi"), entries)

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
        # A new reading is a new decision: forget that the operator once
        # overrode the identification of whatever was plugged in before.
        self._rtnode_manual = False
        self._rtnode_auto_gate = None
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
            elif self._declared_board_key:
                # The operator already picked this board AND confirmed it against
                # its photograph. Detection does not get to ask again — but it
                # DOES get to disagree, and a disagreement is worth stopping for:
                # it means the board in the medic is not the board they think
                # they are holding.
                keys = [b.key for b in (res.get("boards") or [])]
                if keys and self._declared_board_key not in keys:
                    said = next((b.display_name for b in self._boards
                                 if b.key == self._declared_board_key),
                                self._declared_board_key)
                    # Local import: test_guide_resume runs this function's
                    # extracted source in a bare namespace, where the
                    # module-level tr does not exist (i18n wrap, 2026-09-15).
                    from ui.i18n import tr
                    self._declared_mismatch = tr(
                        "You confirmed {said}, but the board plugged in reads as "
                        "{chip}. Check which board is in the medic before "
                        "flashing anything.").format(
                            said=said,
                            chip=res.get("platform") or res.get("chip"))
                    self._sel_board = None       # now it IS worth asking
            elif res.get("board_key"):
                self._sel_board = next(
                    (b for b in self._boards if b.key == res["board_key"]), None)
        self._build_chooser()

    def _detect_summary(self):
        d = self._detected or {}
        if not d.get("found"):
            return d.get("reason", tr("No board detected."))
        fw = (d.get("firmware") or ["rnode"])[0]
        fw_short = FIRMWARE_LABEL.get(fw, fw).split("  ")[0]
        from ui.usb_ports import describe_port
        return tr("Detected {chip} on {port}  -  suggests {fw}").format(
            chip=d.get("platform", d.get("chip")),
            port=describe_port(d.get("port")) or "USB",
            fw=fw_short)

    def _choose_firmware(self):
        entries = [(i, tr(label), lambda k=key: self._pick_firmware(k))
                   for i, (key, label) in enumerate(FIRMWARE_CHOICES, 1)]
        self._picker_popup(tr("Choose firmware"), entries)

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
        """Display name of a positively-identified board with no RTNode-2400
        build; None otherwise. Logic (and the reasoning) in ui.rtnode_choice."""
        try:
            from ui.rtnode_choice import blocked_board
            return blocked_board(self._detected)
        except Exception:
            return None                           # never block on an error

    def _rtnode_target_options(self):
        """The board cards to show — see ui.rtnode_choice.target_options."""
        try:
            from ui.rtnode_choice import target_options
            return target_options(self._detected)
        except Exception:
            from ui.rtnode_choice import HELTEC_PAIR
            return list(HELTEC_PAIR)

    def _rtnode_identified(self):
        """The target when detection identified the board outright, else None
        — the only case in which the chooser screen is skipped."""
        try:
            from ui.rtnode_choice import identified_target
            return identified_target(self._detected)
        except Exception:
            return None

    def _block_rtnode_deadend(self):
        """The popup dead-end: no choices, confirm goes home."""
        if getattr(self, "_rtblock_pop", None) is not None:   # open-once
            return
        from ui.requirement_popup import requirement_popup
        view = requirement_popup(
            tr("This board cannot currently be flashed as an RTNode-2400."),
            tr("Not available for this board"), False)
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
            from ui.usb_ports import describe_port
            txt = tr("Detected {chip} on {port}  ·  {fw}").format(
                chip=d.get("platform", d.get("chip", "board")),
                port=describe_port(d.get("port")) or "USB", fw=fam)
        else:
            txt = fam
        row.add_widget(_line(txt, size="12.5sp", color="green"))
        change = Button(text=tr("change"), size_hint=(None, 1), width=dp(84),
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
        """Board-photo chooser: which Heltec is in front of the operator.

        SKIPPED when the medic already knows. The V3 speaks through a CP2102
        bridge and the V4 is native USB, so detection separates them outright;
        asking a question the tool has already answered is a step the operator
        can only get wrong (operator, 2026-08-18: "if the medic can tell these
        boards apart we need to remove this screen and just move on to the next
        one"). The confirmation gate still stands — it is the brick guard.
        """
        from ui.widgets.board_card import BoardCard
        from ui import board_images
        opts = self._rtnode_target_options()
        ident = self._rtnode_identified()
        if ident and not getattr(self, "_rtnode_manual", False):
            key = ident
            self._rtnode_target = key
            self.header.add_widget(_line(
                tr("Board identified: {board} — read from the "
                   "USB connection, not guessed. Confirm it on the next "
                   "screen.").format(board=board_images.label(key)),
                size="13.5sp", color="accent"))
            token = (self._detected or {}).get("board_key")
            if getattr(self, "_rtnode_auto_gate", None) != token:
                self._rtnode_auto_gate = token
                Clock.schedule_once(
                    lambda dt, k=key: self._confirm_board_gate(k), 0)
            return
        from ui import board_images as _bi
        names = " / ".join(_bi.label(k) or k for k in opts)
        self.header.add_widget(_line(
            tr("Which board is it?  Tap the one in front of you ({names} — "
               "check the name printed on the board).").format(names=names),
            size="13.5sp",
            color="accent"))
        self.header.add_widget(self._warning_box(tr(
            "WARNING:  Selecting the wrong board can brick the hardware. Check your "
            "selection before you build.")))
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(178),
                        spacing=dp(10))
        self._rtnode_cards = {}
        nm = self._name_in.text.strip()
        for key in opts:
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
        self.header.add_widget(_line(tr(
            "Tap the board in front of you — you'll confirm it before "
            "anything builds."), size="12.5sp", color="text_secondary"))
        # T-Beam Supreme (SD transport) is a rarer RTNode target — keep it reachable
        # without cluttering the common V3/V4 choice.
        other = Button(text=tr("Other RTNode board (T-Beam Supreme)…"),
                       size_hint_y=None,
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

    def _warn_build_running(self):
        """Say WHICH build is running and take the operator to it.

        These call sites used to `return` silently, so tapping Build during a
        build did nothing at all — indistinguishable from a dead button, and the
        operator taps again (which is how the "phantom flash" reports started).
        The app already tracked the label and start time; nothing was asking it.
        """
        try:
            from kivy.app import App
            from ui.requirement_popup import requirement_popup
            app = App.get_running_app()
            label, secs = app.activity_info()
            mins = int(secs // 60)
            when = ((tr("1 minute in") if mins == 1
                     else tr("{mins} minutes in").format(mins=mins))
                    if mins else tr("just started"))
            view = requirement_popup(
                tr("{label}\n\nIt's {when} and still going. Starting another "
                   "build now would interrupt it and can leave a board "
                   "half-written.\n\nLet it finish — this screen shows its "
                   "progress and the green confirmation when it's "
                   "done.").format(label=label, when=when),
                tr("A build is already running"), False)

            def _to_progress(*_a):
                try:
                    App.get_running_app().switch_mode("birth")
                    from kivy.clock import Clock as _C
                    _C.schedule_once(lambda _dt: self._scroll_to_progress(), 0.25)
                except Exception:
                    pass
            view.bind(on_dismiss=_to_progress)
        except Exception:
            pass

    def _scroll_to_progress(self):
        """Put the running build's log in view."""
        try:
            self.scroll.scroll_y = 0.0
        except Exception:
            pass

    def _labelled_row(self, label, widget, label_w=118):
        """Heading on the LEFT, its control on the RIGHT, one line instead of two.

        Stacked heading-above-control ate two rows of vertical space per field
        and pushed the green start button below the fold, where it reads as
        absent rather than hidden (operator, 2026-08-02). On a 5-inch panel the
        headings are short and the space beside them is dead, so put them there.
        """
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=max(dp(46), getattr(widget, "height", dp(46))),
                        spacing=dp(8))
        lbl = _line(label, bold=True, size="15sp", color="accent")
        lbl.size_hint_x = None
        lbl.width = dp(label_w)
        row.add_widget(lbl)
        row.add_widget(widget)
        return row

    @staticmethod
    def _pi_needs_imaging():
        """True when the attached Pi has no OS yet (boot-ROM or card-reader).

        Best-effort and fails CLOSED-ish: if we can't tell, we say no and let
        the normal address flow run, because wrongly claiming a working Pi is
        blank would send the operator to reimage a node that was fine.
        """
        try:
            import subprocess
            from provisioning import pi_usbboot
            out = subprocess.run(["lsusb"], capture_output=True, text=True,
                                 timeout=8).stdout
            return pi_usbboot.classify(out).state in (pi_usbboot.BOOTROM,
                                                      pi_usbboot.CARD_READER)
        except Exception:
            return False

    def _go_image_pi(self):
        """Hand off to the card-imaging screen, carrying the node's name.

        The operator named this node one screen ago. Making them type it again
        invites two different names for one node, and the hostname is what the
        medic resolves later to find it.
        """
        name = ""
        try:
            name = (self._name_in.text or "").strip()
        except Exception:
            name = ""
        try:
            from kivy.app import App
            app = App.get_running_app()
            scr = getattr(app, "pi_imager_screen", None)
            # Switch FIRST: entering pi_imager resets it for a new card (and
            # rebuilds the form), which would otherwise wipe what we set here.
            app.switch_mode("pi_imager")
            if scr is not None and hasattr(scr, "prefill_hostname"):
                scr.prefill_hostname(name)
            if scr is not None and hasattr(scr, "set_pi_name"):
                scr.set_pi_name(next(
                    (n for k, n in PI_HOSTS
                     if self._sel_pi and k == self._sel_pi[0]), ""))
        except Exception:
            pass

    def arrived_from_imaging(self, node_name=""):
        """Say where we are. BIRTH is the FINISH of the card step, not a restart.

        The screen is titled "Birth a new node", so arriving here straight after
        writing a card reads as being dumped back at the beginning — the
        operator reported it as looping (2026-08-02). It is in fact the handoff,
        pre-filled with everything they just built; it only needed to say so.
        """
        if self._busy_with_a_build():
            self._warn_build_running()    # don't rebuild the page mid-flash
            return
        self._fresh_lap()                   # no stale board from the last lap
        self._from_imaging = (node_name or "").strip() or True

    def rescan_after_imaging(self):
        """Come back from card imaging and look again.

        The Pi that sent us there was in boot-ROM mode with a blank card. It now
        has an operating system, so the answer to ``_pi_needs_imaging()`` has
        changed — without a re-detect the screen would still be offering to
        image the card we just wrote.

        Detection is cheap and idempotent; if the operator hasn't power-cycled
        the Pi yet it simply reports the same state as before and the imaging
        offer stands, which is the correct thing to show.
        """
        self._detected = None
        try:
            self._detect_board()
        except Exception:
            pass

    def _power_banner(self):
        """One line in the box, the way out underneath it.

        Trimmed on the bench (operator, 2026-08-02): the warning had grown to a
        heading, a reason, a caution and two lists, and the thing they actually
        needed — use a pairing that works — was buried in the middle of it. Now
        the box carries a single sentence, and everything else lives outside it
        as OPTIONS, with the powered hub last and small: it is the workaround,
        not the recommendation.

        Returns True when the pairing is blocked.
        """
        try:
            pi_key = self._sel_pi[0] if self._sel_pi else ""
            board = self._sel_board
            bkey = getattr(board, "key", "") if board is not None else ""
            if not pi_key or not bkey:
                return False
            from workflows.power_compat import check as _check, short_board_name
            v = _check(pi_key, bkey)
            if not v or v.get("verdict") == "ok":
                return False
            from ui.widgets.callout import Callout
            # Local import: the banner's guard test runs this function's
            # extracted source in a bare namespace, where the module-level
            # tr does not exist (i18n wrap, 2026-09-15).
            from ui.i18n import tr
            pi_name = next((n for k, n in PI_HOSTS if k == pi_key), pi_key)
            short = short_board_name(bkey, getattr(board, "display_name", ""))
            self.header.add_widget(Callout(
                tr("{pi} may not power {board} over USB").format(
                    pi=pi_name, board=short)))
            return v.get("verdict") == "blocked"
        except Exception:
            return False                # never block the flow on advice

    def _offer_hub_route(self):
        """If a powered hub is plugged in, ask whether it ships with the node.

        Returns True when it has taken over the panel. Deliberately a question:
        the brown-out happens in the FIELD, and a hub sitting on the bench
        proves the operator owns one, not that the finished node gets it.
        """
        try:
            from provisioning.usb_hub import powered_hub_present, hub_question
            from workflows.power_compat import short_board_name
            hub = powered_hub_present()
            if not hub:
                return False
            pi_key = self._sel_pi[0] if self._sel_pi else ""
            pi_name = next((n for k, n in PI_HOSTS if k == pi_key), "this Pi")
            board = self._sel_board
            short = short_board_name(getattr(board, "key", ""),
                                     getattr(board, "display_name", "the radio"))
            q = hub_question(hub, pi_name, short)
            self.header.add_widget(_line(q["head"], bold=True, size="16sp",
                                         color="green"))
            self.header.add_widget(_line(q["body"], size="14sp",
                                         color="text_primary"))
            go = Button(text=q["confirm"], size_hint_y=None, height=dp(54),
                        bold=True, font_size="15.5sp", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
            go.bind(on_release=lambda *_: self._go_image_pi())
            self.header.add_widget(go)
            self.header.add_widget(_line(q["note"], size="12.5sp",
                                         color="amber"))
            self.header.add_widget(_line(tr("Options"), bold=True, size="16sp",
                                         color="accent"))
            self._recommend_button()
            self._back_home_button()
            return True
        except Exception:
            return False                # no evidence -> keep the refusal

    def _recommend_button(self):
        """Bold: find hardware that actually works with what you have."""
        b = Button(text=tr("Use a Pi and radio board from the suggested list"),
                   size_hint_y=None, height=dp(56), bold=True, font_size="16sp",
                   background_normal="",
                   background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                   color=theme.hex_to_rgba(theme.COLORS["background"]))
        b.bind(on_release=lambda *_: self._show_recommendations())
        self.header.add_widget(b)

    def _back_home_button(self):
        """No forward action from a blocked pairing — only out."""
        b = Button(text=tr("←  Back"), size_hint_y=None, height=dp(50),
                   font_size="16sp", background_normal="",
                   background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                   color=theme.hex_to_rgba(theme.COLORS["text_primary"]))

        def _out(*_a):
            from kivy.app import App
            App.get_running_app().switch_mode("home")
        b.bind(on_release=_out)
        self.header.add_widget(b)

    def _show_recommendations(self):
        """Pairings that run with no powered hub, best margin first.

        Answers the question a refused build actually raises — "then what DO I
        use?" — with the Pi they already have listed first, since that is the
        cheapest way out (operator, 2026-08-02).
        """
        from kivy.uix.popup import Popup
        from kivy.uix.scrollview import ScrollView
        from workflows.power_compat import recommended_pairings
        pi_key = self._sel_pi[0] if self._sel_pi else ""
        box = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(8))
        box.add_widget(_line(tr(
            "These run without a powered hub. Bigger spare current = more "
            "headroom when the radio transmits."), size="14sp",
            color="text_secondary", h=48))
        body = ScrollView()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(6))
        col.bind(minimum_height=col.setter("height"))
        try:
            pairs = recommended_pairings(limit=10, pi_key=pi_key)
        except Exception:
            pairs = []
        yours = [p for p in pairs if p.get("pi_key") == pi_key]
        others = [p for p in pairs if p.get("pi_key") != pi_key]
        if yours:
            col.add_widget(_line(tr("With the Pi you already have"), bold=True,
                                 size="15sp", color="green", h=28))
            for p in yours:
                col.add_widget(_line(
                    "  " + tr("{board}   —  {margin} mA to spare").format(
                        board=p["board"], margin=p["margin_ma"]),
                    size="15sp", h=28))
        if others:
            col.add_widget(_line(tr("With a different Pi"), bold=True, size="15sp",
                                 color="accent", h=32))
            for p in others:
                col.add_widget(_line(
                    "  " + tr("{board}   —  {margin} mA to spare").format(
                        board=p["text"], margin=p["margin_ma"]),
                    size="14sp",
                    color="text_secondary", h=26))
        if not pairs:
            col.add_widget(_line(tr("No pairing data available."), size="14sp",
                                 color="amber", h=30))
        body.add_widget(col)
        box.add_widget(body)
        popup = Popup(title=tr("Combinations that work"), content=box,
                      size_hint=(0.92, 0.86))
        close = Button(text=tr("Close"), size_hint_y=None, height=dp(50),
                       bold=True, background_normal="",
                       background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                       color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        close.bind(on_release=popup.dismiss)
        box.add_widget(close)
        popup.open()

    def _busy_with_a_build(self) -> bool:
        """True while a flash/build is actually RUNNING — resetting state under a
        live workflow corrupts the checklist and the outcome (2026-08-01 bug
        hunt).

        Deliberately does NOT consult ``_flash_view``. That flag is view state —
        which header is showing — not whether hardware is being written, and it
        stays True across the whole certificate page, long after
        ``_mark_activity(False)`` has run in ``_finish()``. Treating it as "busy"
        meant that any exit from the cert page that wasn't the Done button left
        every later build permanently refused, with two stacked "a build is
        already running" popups bouncing the operator onto a finished build
        (audit, 2026-08-03)."""
        try:
            from kivy.app import App
            app = App.get_running_app()
            return bool(app is not None and app.flash_in_progress())
        except Exception:
            return False

    def _fresh_lap(self):
        """Drop everything SELECTION-shaped from the previous build.

        This screen is reused across builds, and a stale ``_sel_board`` silently
        SKIPPED the board pick + confirm gate and offered the last lap's board —
        a V3 nearly flashed as a 'Heltec V4', caught live 2026-08-01. It only
        got reset on the guided path, so the other four ways into BIRTH each
        carried the previous lap's selection in (audit, 2026-08-03).

        Deliberately does NOT clear the per-caller prefills (``_prefill_location``,
        ``_from_imaging``, the name field). Every caller resets FIRST and applies
        its own prefill after, so a shared reset must not undo what the caller is
        about to set. begin_guided clears those separately, because a fresh
        guided lap really does start from nothing.

        Sets ``_lap_prepared`` so ``enter_birth`` knows this lap is already
        clean and does not reset a second time — which would rebuild the chooser
        out from under a just-prefilled name field."""
        self._sel_board = None
        self._sel_pi = None
        self._detected = None
        self._flash_radio = True     # a stale False must never leak into the next lap
        self._rtnode_target = None
        self._firmware = None
        self._declared_board_key = None
        self._declared_mismatch = ""
        self._declared_pi_address = ""
        # One node's radio serial must never become the next node's udev rule —
        # begin_guided re-sets it from its own hand-off after this reset.
        self._guided_radio_usb_serial = ""
        self._guided_radio_usb_serial_source = ""
        self._guided_board_source = ""
        # And one node's Bluetooth yes must never become the next node's radio.
        self._guided_bluetooth = False
        # The previous build's page is over; whoever calls _build_chooser next
        # puts the chooser back, so the flag has to agree or it describes a
        # header that is no longer on screen.
        self._flash_view = False
        self._lap_prepared = True

    def enter_birth(self):
        """Called by ``switch_mode`` BEFORE the transition, on EVERY entry to
        BIRTH — the one choke point that catches entries nobody thought to wire
        up, including future ones.

        Does nothing while a build is running (it owns the screen), and nothing
        when a caller already prepared this lap via _fresh_lap — otherwise a
        plain reset here would wipe the name/location they just stamped on."""
        if self._busy_with_a_build():
            return
        if getattr(self, "_lap_prepared", False):
            self._lap_prepared = False
            return
        self._fresh_lap()
        self._lap_prepared = False
        self._build_chooser()

    def begin_guided(self, path, name=None, board_key=None, pi_key=None,
                     pi_address=None, share_location=None,
                     radio_usb_serial=None, bluetooth=None, location=None,
                     flash_radio=True, radio_usb_serial_source=None,
                     board_source=None):
        """Arrived from the step-by-step guide. Pre-scope the firmware for the chosen
        kind (radio = let detection decide; host = RNode; pi = Pi + RNode) and
        auto-run detection, since the board is already plugged in per the guide — so
        the operator lands on naming + a suggested setup, not a cold form.

        *name* is the node name the guide already asked for, applied HERE rather
        than by a following prefill_name() call. That ordering was the bug: the
        caller had to scope first (begin_guided resets the form) and prefill
        second — but prefill_name calls _fresh_lap() too, which clears
        ``_firmware``. So the scoping was undone by the very next line, and the
        operator arrived on the full unscoped chooser being asked to pick
        RNode / RTNode-2400 / Pi + RNode all over again (live, 2026-08-09).
        One call, one reset, no order to get wrong.

        *board_key* is the radio the operator already picked AND confirmed
        against its photograph in the guide. Without it this screen asked a
        THIRD time — "Detected ESP32-S3, which board is this?" — with a grid of
        near-identical S3 boards (operator, live, 2026-08-09: "we have already
        selected the radio board, is this screen necessary?"). It isn't: the
        answer is known, and every extra pass over that grid is another chance
        to tap the wrong one and flash the wrong image. Detection still runs and
        still gets to DISAGREE — see _detected_done.

        *flash_radio* False means the guide's "I already have a working
        radio" answer (2026-09-06): this radio is never going to be plugged
        into the medic at all, so there is no image to choose and nothing to
        identify — "Board (radio)" would otherwise block a Pi build asking
        the operator to name a board the medic will never touch.
        """
        if self._busy_with_a_build():
            self._warn_build_running()    # never reset under a running build
            return
        self._fresh_lap()
        self._flash_radio = bool(flash_radio)
        if name is not None and getattr(self, "_name_in", None) is not None:
            self._name_in.text = str(name)
        if board_key:
            self._declared_board_key = board_key
            self._sel_board = next((b for b in self._boards
                                    if b.key == board_key), None)
        if pi_key:
            names = dict(PI_HOSTS)
            if pi_key in names:
                self._sel_pi = (pi_key, names[pi_key])
        if pi_address:
            self._declared_pi_address = pi_address
        # Cleared here too, or every later visit to BIRTH shows the green
        # "Card written — now building <the PREVIOUS node>" banner over an
        # empty form (audit, 2026-08-03).
        self._from_imaging = None
        # A map-stamped position belongs to ONE node: left set, every later
        # birth in the session inherited the previous node's coordinates
        # (2026-08-01 bug hunt — a privacy leak as well as a wrong pin).
        # Set unconditionally from THIS hand-off: the guide's prelude map is
        # the one place the pin is placed (operator, 2026-08-14), and a lap
        # that carries none must not inherit the last node's.
        self._prefill_location = ((float(location[0]), float(location[1]),
                                   "map-confirmed")
                                  if location else None)
        # AND SO DOES THE ANSWER ABOUT PUBLISHING IT. Set unconditionally from
        # this hand-off, so a lap that carries no answer (None) normalises to
        # hidden rather than inheriting the last node's yes — the same leak as
        # the line above, but of a decision instead of a fact.
        from monitor import location_share
        self._share_location = location_share.normalise(share_location)
        # The serial the medic read when IT flashed this node's radio. Set
        # unconditionally from the hand-off (same hygiene as the location and
        # the share answer above): a lap that carries none must not inherit
        # the previous node's radio.
        self._guided_radio_usb_serial = (radio_usb_serial or "").strip()
        # ...and its provenance, printed on the certificate beside the
        # serial (2026-09-22). Same hygiene: this hand-off's or nothing.
        self._guided_radio_usb_serial_source = (
            radio_usb_serial_source or "").strip()
        # How the board was known (a workflows.build.BOARD_SOURCES code):
        # this hand-off's or nothing, like everything above.
        self._guided_board_source = (board_source or "").strip()
        # The Bluetooth answer, same hygiene: set unconditionally from this
        # hand-off, so a lap that carries none normalises to OFF (the quiet
        # end) rather than inheriting the last node's yes.
        self._guided_bluetooth = bool(bluetooth)
        self._forced_firmware = {"radio": "rtnode2400", "host": "rnode",
                                 "pi": "pi_rnode"}.get(path)
        if self._forced_firmware:
            self._firmware = self._forced_firmware
        self._build_chooser()
        # DON'T GO LOOKING FOR A BOARD THAT ISN'T HERE. The Pi path's final
        # hand-off arrives with the radio already flashed and already moved onto
        # the Pi — so a USB scan finds nothing and the screen greets the end of a
        # successful walkthrough with "No work board on the medic's USB"
        # (audit, 2026-08-09). The board is not unknown; it was chosen,
        # confirmed, flashed and verified several steps ago.
        if path == "pi" and board_key:
            return
        self._detect_board()

    def _choose_rtnode_target(self):
        entries = [(i, t.display, lambda k=key: self._pick_rtnode_target(k))
                   for i, (key, t) in enumerate(RTNODE_TARGETS.items(), 1)]
        self._picker_popup(tr("RTNode-2400 target"), entries)

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
        # "Check the silkscreen" answers the V3-or-V4 question; a T-Echo is
        # a cased product with no silkscreen question to answer.
        warn_hint = (tr("Check the silkscreen on the board itself.")
                     if key in ("heltec_v3", "heltec_v4")
                     else tr("Check it against the photo below."))
        warn = Label(
            text=(tr("WARNING:  Selecting the wrong board can BRICK the "
                     "hardware.") + "\n" + warn_hint),
            bold=True, font_size="19sp", color=dark, halign="center",
            valign="middle", size_hint_y=None, height=dp(92))
        warn.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(warn)
        card = BoardCard(key, name=self._name_in.text.strip(), selected=True,
                         on_select=lambda *_: None, size_hint_y=1)
        body.add_widget(card)
        confirm_lbl = Label(
            text=tr("Confirm you've selected the correct board:  "
                    "{board}").format(board=board_images.label(key)),
            bold=True, font_size="16sp", color=dark, halign="center",
            valign="middle", size_hint_y=None, height=dp(44))
        confirm_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(confirm_lbl)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(58), spacing=dp(10))
        back = Button(text=tr("Back — wrong board"), bold=True, font_size="15sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        go = Button(text=tr("Confirm — build RTNode-2400\n({target})").format(
                        target=tgt.display),
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
        pop = Popup(title=tr("Check the board"), content=body,
                    size_hint=(0.95, 0.9),
                    title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                    auto_dismiss=False)
        self._gate_pop = pop
        pop.bind(on_dismiss=lambda *_: setattr(self, "_gate_pop", None))
        def _back(*_a):
            # "Wrong board" after an auto-identified board must hand the choice
            # BACK, or the operator is trapped facing a gate they disagree with
            # and a screen with nothing on it to change.
            self._rtnode_manual = True
            pop.dismiss()
            self._build_chooser()
        back.bind(on_release=_back)

        def _go(btn, *_a):
            btn.disabled = True
            btn.text = tr("Starting build…")
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
            text=(tr("WARNING:  Selecting the wrong board can BRICK the "
                     "hardware.") + "\n"
                  + tr("Check the silkscreen on the board itself.")),
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
            text=tr("Confirm you've selected the correct board:  "
                    "{board}").format(board=board.display_name),
            bold=True, font_size="16sp", color=dark, halign="center",
            valign="middle", size_hint_y=None, height=dp(44))
        confirm_lbl.bind(size=lambda w, s: setattr(w, "text_size", s))
        body.add_widget(confirm_lbl)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(58), spacing=dp(10))
        back = Button(text=tr("Back — wrong board"), bold=True, font_size="15sp",
                      background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        go = Button(text=tr("Confirm — flash as RNode\n({board})").format(
                        board=board.display_name),
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
        pop = Popup(title=tr("Check the board"), content=body,
                    size_hint=(0.95, 0.9),
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
                              getattr(workflow, "title", tr("Heads up")),
                              getattr(workflow, "under_construction", False))
            return
        # This path builds its workflow directly, so it must also choose the
        # busy words directly — it never passes through _build_workflow, where
        # busy_truth() is called. Without this the safety banner kept the LAST
        # run's text: a V3 RTNode build ran for its whole length under "Flashing
        # RNode / Heltec LoRa32 v4", naming the wrong board and the wrong
        # firmware on the one line telling the operator not to power off
        # (bench, 2026-08-18).
        from ui.busy_truth import busy_truth
        self._busy_banner, self._busy_paragraph = busy_truth(
            "rtnode2400", None, self._name_in.text.strip(),
            guided=self._guided_pending())
        self._launch(workflow, tr("Building RTNode-2400 ({target})…").format(
            target=tgt.display))

    def _show_power_popup(self, verdict, board_name, pi_key, on_proceed,
                          board_key=""):
        """Warn that this Pi can't power this board over USB — Proceed (⚠ red,
        bottom-left) / Cancel (green, bottom-right). *board_key* lets the
        bench chart (workflows.pairing_verdicts) add its dated provenance
        line — warn, never hard-block: it is the operator's bench, and a
        deliberate retest is theirs to run."""
        pi_name = next((n for k, n in PI_HOSTS if k == pi_key), pi_key)
        # The text can run long (warning + why + remedies + suggestions), and
        # every child here has a FIXED height — so without a scroll the buttons
        # get pushed off the bottom of the popup and become untappable. Same
        # trap that hid the vault reset slider (operator report 2026-08-01).
        root = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(6))
        from kivy.uix.scrollview import ScrollView
        scroll = ScrollView(size_hint=(1, 1), do_scroll_x=False, bar_width=dp(4))
        body = BoxLayout(orientation="vertical", spacing=dp(8),
                         size_hint_y=None, padding=[0, 0, dp(6), 0])
        body.bind(minimum_height=body.setter("height"))
        scroll.add_widget(body)
        root.add_widget(scroll)
        # The COPY lives in workflows.power_compat.warning_lines() — pure, so
        # the wording is unit-tested without a display, and the advice can
        # never drift from the power model that produced the verdict.
        from workflows.power_compat import warning_lines
        _STYLE = {"head":   dict(bold=True, size="16sp", color="amber"),
                  "warn":   dict(size="13.5sp", color="amber"),
                  "body":   dict(size="14sp"),
                  "bullet": dict(size="12sp", color="text_secondary"),
                  "good":   dict(size="12.5sp", color="green")}
        try:
            lines = warning_lines(verdict, pi_name, board_name, pi_key,
                                  board_key=board_key)
        except Exception:                                  # never block a birth
            lines = [{"kind": "head",
                      "text": (verdict or {}).get("why", tr("Power warning"))}]
        for ln in lines:
            style = dict(_STYLE.get(ln["kind"], _STYLE["body"]))
            if ln["kind"] == "good" and ln["text"].endswith(":"):
                style["bold"] = True
            body.add_widget(_line(ln["text"], **style))
        btns = BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=dp(56), spacing=dp(10))
        proceed = Button(text=tr("⚠  Proceed anyway"), font_size="16sp",
                         bold=True, background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["red"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        cancel = Button(text=tr("Cancel"), font_size="16sp", bold=True,
                        background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                        color=theme.hex_to_rgba(theme.COLORS["background"]))
        btns.add_widget(proceed)       # bottom-left
        btns.add_widget(cancel)        # bottom-right
        root.add_widget(btns)          # OUTSIDE the scroll — always reachable
        popup = Popup(title=tr("Power warning"), content=root,
                      size_hint=(0.92, 0.86),
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
        # The five radio fields are correct by default and almost never touched,
        # but expanded they pushed the green start button off the bottom of the
        # screen — where it simply looks absent unless you already know to
        # scroll (operator, 2026-08-02). Collapsed behind a summary line, the
        # start button fits on the same screen; open them and you scroll past
        # them, which is the right way round.
        fields = [
            ("freq", "Frequency (MHz)", f"{dd['freq']:g}"),
            ("bw", "Bandwidth (kHz)", f"{dd['bw']:g}"),
            ("sf", "Spreading factor", str(dd['sf'])),
            ("cr", "Coding rate", str(dd['cr'])),
            ("txp", "TX power (dBm)", str(dd['txp'])),
        ]
        summary = (f"{dd['freq']:g} MHz · BW{dd['bw']:g} · SF{dd['sf']} · "
                   f"CR{dd['cr']} · {dd['txp']} dBm")
        self._params_open = False
        self._params_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                     spacing=dp(6))
        self._params_box.bind(minimum_height=self._params_box.setter("height"))
        self._params_box.height = 0

        toggle = Button(size_hint_y=None, height=dp(54), font_size="15sp",
                        halign="left", valign="middle", background_normal="",
                        background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        toggle.bind(size=lambda i, v: setattr(i, "text_size",
                                              (v[0] - dp(24), v[1])))

        def _sync_toggle():
            arrow = "▾" if self._params_open else "▸"
            toggle.text = f"  {arrow}  " + tr("Radio parameters") + f"     {summary}"

        def _toggle(*_a):
            self._params_open = not self._params_open
            self._params_box.clear_widgets()
            if self._params_open:
                for key, label, value in fields:
                    self._params_box.add_widget(
                        self._param_row(key, label, value))
            else:
                # keep the inputs alive so edits survive collapsing
                pass
            _sync_toggle()

        _sync_toggle()
        toggle.bind(on_release=_toggle)
        self.list.add_widget(toggle)
        self.list.add_widget(self._params_box)

        # Build the inputs up front (hidden) so _confirm_params always has them,
        # whether or not the operator ever opened the drawer.
        for key, label, value in fields:
            if key not in self._param_inputs:
                self._param_row(key, label, value)

        ok = Button(text=tr("OK — start"), size_hint_y=None, height=dp(60),
                    font_size="20sp", bold=True, background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                    color=theme.hex_to_rgba(theme.COLORS["background"]))
        ok.bind(on_release=lambda *_a: self._confirm_params(node_type, board))
        self.list.add_widget(ok)

    def _param_row(self, key, label, value):
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(50),
                        spacing=dp(8))
        row.add_widget(_line(tr(label), size="15sp"))
        ti = TextInput(text=value, multiline=False, size_hint=(None, None),
                       width=dp(160), height=dp(44), font_size="25sp",
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
        msg = Label(halign="center", valign="middle", markup=True, text=tr(
            "[b]You're about to change the radio parameters.[/b]\n\n"
            "It is STRONGLY advised to keep the preset parameters\n"
            "[b]{presets}[/b]\n"
            "so that ALL nodes can talk to each other. A node built on "
            "different settings CANNOT hear the rest of the mesh.\n\n"
            "To change the settings every build uses, do it once in\n"
            "Settings ▸ Default radio parameters.").format(
                presets=rd.summary(rd.load_defaults())),
            color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(8))
        popup = Popup(title=tr("Changing radio parameters"), content=box,
                      size_hint=(0.94, 0.7),
                      title_color=theme.hex_to_rgba(theme.COLORS["red"]),
                      separator_color=theme.hex_to_rgba(theme.COLORS["red"]))
        self._pw_pop = popup
        popup.bind(on_dismiss=lambda *_: setattr(self, "_pw_pop", None))
        keep = Button(text=tr("Keep presets"), bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))

        def _keep(*_):
            popup.dismiss()
            try:
                field.focus = False           # back away from the field
            except Exception:
                pass
        keep.bind(on_release=_keep)
        edit = Button(text=tr("⚠  Edit anyway"), bold=True,
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
        msg = Label(halign="center", valign="middle", markup=True, text=tr(
            "[b]Keep the standard parameters?[/b]\n\n"
            "It is STRONGLY recommended to build every node with the standard "
            "settings\n[b]{standard}[/b]\n"
            "so that ALL nodes can communicate with each other.\n\n"
            "A node built with different parameters CANNOT hear the rest of "
            "the mesh.\n\nYou entered:\n[b]{yours}[/b]").format(
                standard=rd.summary(rd.DEFAULT_PARAMS),
                yours=rd.summary(radio)),
            color=theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
        msg.bind(size=lambda i, v: setattr(i, "text_size", v))
        box.add_widget(msg)
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(8))
        popup = Popup(title=tr("Non-standard radio parameters"), content=box,
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
        keep = Button(text=tr("Keep standard"), bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        keep.bind(on_release=_keep)

        def _anyway(*_):
            popup.dismiss()
            proceed()
        anyway = Button(text=tr("⚠  Use anyway"), bold=True, background_normal="",
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
            requirement_popup(workflow.message,
                              getattr(workflow, "title", tr("Heads up")),
                              getattr(workflow, "under_construction", False))
            return
        pi_key = self._sel_pi[0] if self._sel_pi else "none"
        if pi_key != "none" and board is not None:
            verdict = power_check(pi_key, board.key)
            # Two voices gate here: the current-budget arithmetic (check) and
            # the operator's bench chart (pairing_verdicts) — a coin-flip cell
            # warns even when the arithmetic is content. The chart is ADVICE:
            # if it can't load, fall back to the arithmetic-only gate rather
            # than let advisory code block a birth.
            try:
                from workflows.pairing_verdicts import needs_warning
                warn = needs_warning(pi_key, board.key, verdict)
            except Exception:
                warn = bool(verdict and
                            verdict.get("verdict") in ("blocked", "caution"))
            if warn:
                self._show_power_popup(
                    verdict, board.display_name, pi_key,
                    lambda: self._launch(workflow, title),
                    board_key=board.key)
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
            if prof is not None:
                # THE BOARD THE OPERATOR NAMED, OR NOTHING. NodeProfile's
                # rnode_board_key defaults to heltec32_v4, and with no board
                # picked that default went into the build and onto the
                # certificate as a fact — skyfinger, a RAK4631, was certified
                # as a Heltec V4 (2026-09-22). "" is the honest value: the
                # certificate prints "unknown", and a blank board that turns
                # out to be attached is refused rather than flashed with
                # another board's image.
                prof.rnode_board_key = board.key if board else ""
                # HOW it was known, for the certificate: named from the
                # catalogue on the "already have one" road (the radio was
                # never on this medic), read off this medic's USB and
                # confirmed on the flash road; the guide says which, and
                # a board picked on THIS screen is the operator's naming.
                # THE MEDIC'S OWN TIMEZONE rides to the node (2026-09-23);
                # "" when it cannot be read, and the build step says so.
                try:
                    from provisioning.tool_datetime import current_timezone
                    prof.timezone = current_timezone() or ""
                except Exception:                                  # noqa: BLE001
                    prof.timezone = ""
                prof.rnode_board_source = (
                    "" if not board else
                    (getattr(self, "_guided_board_source", "") or
                     ("operator" if not getattr(self, "_flash_radio", True)
                      else "usb")))
                # The serial the medic read at flash time rides the profile so
                # install_radio_rule can pin /dev/rnode to THIS radio even
                # though the radio is in the operator's pocket during the build.
                prof.radio.usb_serial = getattr(
                    self, "_guided_radio_usb_serial", "") or ""
                prof.radio.usb_serial_source = getattr(
                    self, "_guided_radio_usb_serial_source", "") or ""
                # The birth answer configure_bluetooth applies on the node.
                prof.bluetooth_enabled = bool(getattr(
                    self, "_guided_bluetooth", False))
            title = (tr("Building Pi + {board}...").format(
                         board=board.display_name) if board
                     else tr("Building Pi + RNode..."))
            busy_kind = "pi_rnode"
        elif board is not None:                  # standalone RNode flash (no Pi)
            workflow = self._rnode_flash_factory(board)
            title = tr("Flashing {board}...").format(board=board.display_name)
            busy_kind = "rnode_flash"
        else:                                    # RTNode-2400
            workflow = self._factories[node_type]()
            title = tr("Building {what}...").format(
                what=self._labels.get(node_type, node_type))
            busy_kind = node_type
        # The busy view's words are chosen HERE, the one place that knows what
        # kind of work is coming — a Pi build must not promise a firmware
        # compile, an autoinstall flash writes prebuilt firmware, and only the
        # paths that really compile may say so (ui.busy_truth).
        from ui.busy_truth import busy_truth
        self._busy_banner, self._busy_paragraph = busy_truth(
            busy_kind, board, self._name_in.text.strip(),
            guided=self._guided_pending())
        self._apply_radio(workflow, radio)
        self._apply_location_sharing(workflow)
        return workflow, title

    def _guided_pending(self) -> bool:
        """Is a guided walkthrough waiting for this build to hand back?

        The app owns the answer (ui.app.guided_birth_pending). Asked here as a
        bare name it was a NameError that killed the whole application the
        moment the operator pressed "OK — start", on both the Pi and the
        RTNode roads (live, 2026-09-09). Every other caller in this file
        already went through the app with a getattr default; this is that
        pattern, once, so there is one place to be wrong.
        """
        try:
            from kivy.app import App
            app = App.get_running_app()
            return bool(getattr(app, "guided_birth_pending",
                                lambda: False)())
        except Exception:                                          # noqa: BLE001
            return False

    def _apply_location_sharing(self, workflow):
        """Put this birth's map answer (and the position it governs) onto the
        profile the workflow will build from.

        Both halves are needed and they are different things: the POLICY is the
        operator's answer on the guided share screen, and the POSITION is the
        map-confirmed pin. The workflow fuzzes the second before anything is
        written to the node; the exact value stays here.

        Best-effort by design — a workflow without a profile (a blocked or
        honest-fail stand-in) must not turn a birth into a crash, and the
        absence of a policy means hidden, which is the safe direction.
        """
        prof = getattr(workflow, "profile", None)
        if prof is None:
            return
        prof.share_location = getattr(self, "_share_location", "hidden")
        loc = getattr(self, "_prefill_location", None)
        if loc:
            prof.location = (loc[0], loc[1])

    def _launch(self, workflow, title):
        # Blocked path (no board attached / not wired to real hardware yet): say so
        # in a plain popup instead of faking a run or dumping a failed-step log.
        if getattr(workflow, "is_blocked", False):
            from ui.requirement_popup import requirement_popup
            requirement_popup(workflow.message,
                              getattr(workflow, "title", tr("Heads up")),
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
                    tr("{label}\n\nIt has been running {secs}s — this is "
                       "the build YOU started (the red banner at the top). Let it "
                       "finish before starting another.").format(
                           label=label, secs=int(elapsed)),
                    tr("Your build is running"), False)
                return
        except Exception:
            pass
        # Consume the words chosen for THIS run and clear them, so a launch
        # that forgets to set them gets the honest generic line rather than the
        # previous build's board name.
        self._run_banner = getattr(self, "_busy_banner", None)
        self._run_paragraph = getattr(self, "_busy_paragraph", None)
        self._busy_banner = self._busy_paragraph = None
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
            getattr(self, "_run_paragraph", None)
            or tr("Working… keep everything plugged in and WAIT for the green "
                  "'Build finished' confirmation before touching anything."),
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
        step_display = getattr(workflow, "step_display", {}) or {}
        self._step_display = step_display
        self._step_phase_labels = getattr(workflow, "step_phase_labels", {}) or {}
        for n in self._pg_names:
            row = BoxLayout(orientation="horizontal", size_hint_y=None,
                            height=dp(28), spacing=dp(12))
            # A workflow may rename a step for THIS build — the T-Echo has no
            # WiFi, so a row reading "wifi_onboarding" while USB provisioning
            # runs was a lie in the checklist (review, 2026-08-19).
            lbl = _line("  " + step_display.get(n, n),
                        color="text_secondary", size="13.5sp")
            lbl.size_hint_x = 0.5
            # dp(28) is a FLOOR the row grows past, not a pin. _line already
            # grows the label with its wrapped text, but the row stayed 28
            # tall — so "set_firmware_radio_parameters  (skipped)" wrapped to
            # two lines and drew over its neighbours (operator photo,
            # 2026-08-12). Same floors-not-pins rule as the walkthrough.
            lbl.bind(height=lambda _l, h, r=row: setattr(
                r, "height", max(dp(28), h)))
            bar = _StepBar()
            holder = AnchorLayout(anchor_y="center", size_hint_x=0.5)
            holder.add_widget(bar)
            row.add_widget(lbl)
            row.add_widget(holder)
            self.list.add_widget(row)
            self._step_rows[n] = (lbl, bar)
        # A workflow may size its own steps: the global table said
        # wifi_onboarding=2s while a legal nRF usb_setup runs ~5 minutes —
        # the ring froze at full for the whole step (storm review, 2026-08-20).
        _wf_secs = getattr(workflow, "step_seconds", {}) or {}
        self._pg_secs = [_wf_secs.get(n) or _STEP_SECONDS.get(n, _DEFAULT_STEP_SECONDS)
                         for n in self._pg_names]
        self._pg_total = max(1.0, float(sum(self._pg_secs)))
        self._pg_done = 0                        # completed step count
        self._pg_step_start = time.monotonic()
        self._build_ring.set_fraction(0.0)
        self._pg_ev = Clock.schedule_interval(self._tick_progress, 0.2)
        self._workflow = workflow
        self._had_failure = False                # reset for this run's outcome
        self._failure_messages = []              # what actually failed, verbatim
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
                banner = (getattr(self, "_run_banner", None)
                          or tr("Working — keep everything plugged in, "
                                "don't power off"))
                app.begin_activity(banner)
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
            try:
                self._failure_messages.append(str(getattr(r, "message", "")))
            except Exception:      # noqa: BLE001
                pass
            msg = f"{type(e).__name__}: {e}"
            Clock.schedule_once(lambda dt, m=msg: self.list.add_widget(
                _line(f"  [CRASH] {m}", color="red", size="13sp")), 0)
        finally:
            Clock.schedule_once(lambda dt: self._finish(), 0)

    def _hand_back_to_guide(self):
        """A guided birth sent us here to do one job. Give it back.

        Carries the VERIFY verdict, not "the flash finished" — the radio gate
        must be able to stall on a board that flashed and still does not report
        as an RNode. Delayed a beat so the outcome popup is seen: a screen that
        vanishes the instant it says "failed" has not told anybody anything.

        Silent when no walkthrough is waiting, so a flash started straight from
        the BIRTH screen behaves exactly as it always has.
        """
        try:
            from kivy.app import App
            app = App.get_running_app()
            if not getattr(app, "guided_birth_pending", lambda: False)():
                return
            verified = bool(getattr(self, "_radio_verified", False))
            # AND WHETHER THE BUILD ITSELF LIVED. Without this the walkthrough
            # carried on regardless: the operator dismissed "Build didn't
            # finish", and the very next screen congratulated them — "That's
            # the node built" over instructions to take the Pi away and power
            # it up (operator, 2026-08-10: "this is confusing after a warning
            # saying something didn't work"). A walkthrough that celebrates a
            # failed build sends a dead node into the field.
            failed = bool(getattr(self, "_had_failure", False))
            # AND WHERE IT REACHED THE NODE. The closing screen is the record of
            # what happened, and it was asserting "provisioned over the cable"
            # for a node provisioned over Wi-Fi (SkyFinger, 2026-08-11). The
            # route is not a detail: it is the difference between a node that
            # needs a cable to be repaired and one that does not.
            try:
                reached = (self._pi_addr_in.text or "").strip()
            except Exception:                                  # noqa: BLE001
                reached = ""
            # AND THE RADIO'S OWN SERIAL. The flash captured it (the one thing
            # that survives re-enumeration) and this hand-back used to throw it
            # away — so every Pi shipped with the five-vendor udev net instead
            # of a rule naming ITS radio (2026-08-12 handover). Carrying this
            # one string is what lets /dev/rnode mean "this radio".
            serial = (getattr(self._workflow, "_usb_serial", "") or "")
            payload = {"radio_verified": verified, "build_failed": failed,
                       "reached_at": reached}
            # The [FAIL] line travels WITH the failure (briefing Task 2): the
            # step the operator lands back on shows the reason, not a
            # treasure map to a log pane.
            if failed:
                fl = next(((r.name, r.message) for r in
                           getattr(self._workflow, "results", [])
                           if not r.success
                           and not getattr(r, "skipped", False)), None)
                if fl:
                    payload["fail_line"] = f"[FAIL] {fl[0]} — {fl[1]}"
            # OMIT the key when there is nothing to say. resume() setattrs
            # every key it is given, so the Pi build's hand-back (whose
            # workflow never sees the radio) was overwriting the serial the
            # FLASH had captured with '' — and every retry after a failed
            # build lost the pinned udev rule (live log, 2026-08-12).
            if serial:
                payload["radio_usb_serial"] = serial
                # ...WITH its provenance, or resume() would keep the other
                # road's story next to this road's number (2026-09-22).
                payload["radio_usb_serial_source"] = "flash"
            Clock.schedule_once(
                lambda _dt: app.resume_guided_birth(payload), 2.5)
        except Exception:                                          # noqa: BLE001
            pass            # a failed hand-back must never break the outcome

    def _step(self, result):
        # REMEMBER WHETHER THE RADIO ACTUALLY PASSED. `verify` is the step that
        # asks the board to report as a provisioned RNode, so it — not "the
        # flash returned" — is the honest answer to "have we got a working
        # radio". A board can flash, enumerate and still be useless: the Heltec
        # V4 on the bench on 2026-08-08 was visible all evening while
        # boot-looping every 2.4 seconds. The guided birth's radio gate reads
        # this (see birth_guide_screen._radio_gate).
        if result.name == "verify" and not result.skipped:
            self._radio_verified = bool(result.success)
        mark = "skip" if result.skipped else ("ok" if result.success else "FAIL")
        color = ("text_secondary" if result.skipped
                 else "green" if result.success else "red")
        if not result.success and not result.skipped:
            self._had_failure = True
            try:
                # `r` never existed here: the NameError was swallowed by
                # the except below, so this list stayed EMPTY and every
                # screen that reads it lost the reason a build failed
                # (found by the undefined-name guard, 2026-09-09).
                self._failure_messages.append(
                    str(getattr(result, "message", "")))
            except Exception:      # noqa: BLE001
                pass
        pair = getattr(self, "_step_rows", {}).get(result.name)
        if pair is not None:
            # pre-listed checklist row: fill the bar, colour the text
            lbl, bar = pair
            lbl.color = theme.hex_to_rgba(theme.COLORS[color])
            lbl.bold = not result.skipped
            if result.skipped:
                lbl.text = f"  {result.name}  " + tr("(skipped)")
            bar.set(1.0, color=("green" if result.success or result.skipped
                                else "red"))
        elif not result.skipped:                 # unplanned step -> old style
            # An unplanned SKIP is silence by design: the plan omitted it
            # because it could do nothing here (see planned_step_names).
            # Announcing "[skip]" for it would put the alarming grey row
            # right back. Real unplanned work (ok or FAIL) still surfaces.
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
                cur = names[self._pg_done]
                phases = getattr(self, "_step_phase_labels", {}) or {}
                lbl.text = phases.get(cur) or tr(_PHASE_LABELS.get(cur,
                                                                   "Working…"))
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
                # Recovery advice must match the board in the operator's hand.
                # This said "hold BOOT, tap RST" to everyone, and a RAK4631 has
                # no BOOT button at all — it double-taps RESET into a UF2
                # bootloader. Being told to press a button that does not exist
                # is worse than being told nothing (operator, 2026-08-05).
                # ...AND IT HAS TO MATCH WHAT FAILED, not just which board.
                # A Pi + RNode build is an SSH session to a Raspberry Pi; when
                # it dies, the advice above sent the operator hunting for a PRG
                # button a Pi does not have, and a data cable that was not the
                # problem (operator, live, 2026-08-09 — the node had wedged on
                # a browning-out supply). Advice for the wrong failure is worse
                # than none: it spends the one thing they have least of.
                # THE FAIL LINE ITSELF, in the dialog (briefing Task 2,
                # 2026-08-14: the dialog pointed at a log the UI never
                # showed — two bench runs died without the reason ever
                # reaching the glass).
                fail = next(((r.name, r.message) for r in
                             getattr(self._workflow, "results", [])
                             if not r.success
                             and not getattr(r, "skipped", False)), None)
                fail_line = (f"[FAIL] {fail[0]} — {fail[1]}" if fail
                             else "")
                head = (fail_line + "\n\n" + tr("Fix that and run the build "
                                                "again.")
                        if fail_line else
                        tr("A build step failed — the [FAIL] line in the build "
                           "log names it, with the reason under it. Fix that "
                           "and run the build again."))
                if getattr(self, "_last_type", "") == "pi_rnode":
                    body = head + "\n\n" + tr(
                        "If it failed at the first step, the Pi "
                        "stopped answering. Check its power light, and that "
                        "it is still plugged in — a Pi drawing its power "
                        "from Node Medic can brown out part-way through. "
                        "Its card can be checked too: put it in Node "
                        "Medic's reader and it will say whether the card "
                        "needs writing again.")
                elif _ONBOARDING_FAILURE.search(fail_line or ""):
                    # ADVICE FOR THE FAILURE WE ACTUALLY HAD. wifi_onboarding
                    # fails for reasons that have nothing to do with the board
                    # or the cable — the medic not being on Wi-Fi, an
                    # unreadable PSK, a 5 GHz-only SSID. Offering "hold BOOT,
                    # tap RST, try another cable" there is the 2026-08-09
                    # "advice for the wrong failure" complaint in an unfixed
                    # corner: the flash SUCCEEDED, the setup did not.
                    body = head + "\n\n" + tr(
                        "The board flashed fine — this failed "
                        "at the setup step, so buttons and cables won't "
                        "help. Fix what the line above names, then run the "
                        "build again. You can also set the node up by hand "
                        "at its own portal: join its 'RTNode-Setup' Wi-Fi "
                        "and open http://10.0.0.1.")
                else:
                    from ui.safety import recovery_for_board
                    recover = recovery_for_board(getattr(self, "_last_board", None))
                    body = head + "\n\n" + tr(
                        "Board won't flash?  {recover}  If it "
                        "still won't, try a short, known-good USB data "
                        "cable.").format(recover=recover)
                # Show-don't-tell: the recovery text names buttons to press, so
                # show the board they're on. image_for() returns None when we
                # have no photo for this board — then it's the old text-only
                # card, unchanged (UX review 2026-08-14: "never shows the board
                # it's telling the operator to press buttons on").
                from ui import board_images
                board_png = board_images.image_for(getattr(self, "_last_board", None))
                view = requirement_popup(body, tr("Build didn't finish"), False,
                                         image_path=board_png)
                # Bring the chooser back so the operator can rerun, but KEEP
                # the log below — it names what failed.
                view.bind(on_dismiss=lambda *_:
                          self._exit_flash_view(keep_list=True))
                return
            onboarding = getattr(self._workflow, "onboarding", None)
            nm = (onboarding or {}).get("node_name", "") or tr("the node")
            # IS THIS THE END, OR A WAYPOINT? Green is the colour of arrival,
            # and it was being spent on both. Flashing the radio cleanly at
            # step 5 of a 10-step Pi build raised the full green "Build
            # finished" card — "too final for the successful radio flash
            # before the process moves to the pi sd provisioning" (operator,
            # 2026-09-07), with a "Got it" that reads as a goodbye.
            #
            # guided_birth_pending() is the honest test: a walkthrough waiting
            # for the hand-back HAS more screens to show, so the build that
            # just finished cannot be the last thing that happens. Same words,
            # blue card, "Continue" — the good news without the false ending.
            # A flash started straight from BIRTH has nothing waiting and stays
            # green, exactly as before.
            try:
                from kivy.app import App
                _app = App.get_running_app()
                more_to_come = bool(
                    getattr(_app, "guided_birth_pending", lambda: False)())
            except Exception:                                      # noqa: BLE001
                more_to_come = False
            _tone = "progress" if more_to_come else "success"
            _btn = tr("Continue") if more_to_come else None
            if onboarding:
                # THE TEST HAS TO BE ONE THE KEEPER CAN RUN. This told them to
                # read the board's screen — and the catalogue records boards
                # with no screen at all (XIAO ESP32S3, RAK4631), so on those
                # it named the one check they cannot perform and left them no
                # way to tell which of the two outcomes they got (audit,
                # 2026-09-09). VITALS works on every board, so it leads; the
                # CONFIG MODE sentence stays only where there is a display to
                # read it on.
                try:
                    from ui.board_images import has_screen as _has_screen
                    _screened = _has_screen(
                        getattr(getattr(self, "_last_board", None), "key", "")
                        or "")
                except Exception:                                  # noqa: BLE001
                    _screened = False
                _setup = (tr("The setup details are printed in the build log — "
                             "join the 'RTNode-Setup' WiFi and enter them at "
                             "http://10.0.0.1."))
                if _screened:
                    _how = tr("If its screen still says CONFIG MODE, it still "
                              "needs setting up. {setup} If it shows its "
                              "status screen, it's already configured — watch "
                              "VITALS for its first health beacon.").format(
                                  setup=_setup)
                else:
                    _how = tr("Watch VITALS for its first health beacon — that "
                              "is this node saying it is up and configured. If "
                              "nothing arrives, it is still waiting. "
                              "{setup}").format(setup=_setup)
                view = requirement_popup(
                    tr("Build finished for {name}.").format(name=nm)
                    + "\n\n" + _how,
                    tr("Build finished"), False, tone=_tone, button_text=_btn)
            elif getattr(self, "_last_type", "") == "pi_rnode":
                # Name the physical action — WHEN THERE IS ONE. "Unplug BOTH
                # boards from Node Medic" is only true if something is
                # actually on the medic: the radio (flashed here) or the Pi
                # (reached over its own cable). Live, 2026-09-06: an operator
                # whose Pi already carried a working radio, powered itself,
                # reached only over Wi-Fi, was told to unplug two boards that
                # were never plugged in — asking them to undo work that had
                # never been done. "if the node medic registers that the pi is
                # not plugged into the node medic then this message does not
                # need to appear — you can just say finished" (operator).
                radio_on, pi_on = self._whats_on_the_medic()
                on_medic = radio_on or pi_on
                if on_medic:
                    b = getattr(self, "_last_board", None)
                    bn = b.display_name if b is not None else tr("the radio")
                    # Name only the unplugs that are REAL. Three cases, and
                    # the old copy asserted the first for all of them.
                    if radio_on and pi_on:
                        what = tr("Unplug both boards from Node Medic")
                    elif pi_on:
                        what = tr("Unplug the Pi from Node Medic")
                    else:
                        what = tr("Take the radio off Node Medic")
                    view = requirement_popup(
                        tr("Built — but not finished yet.\n\n{what}, plug {board} "
                           "into the Pi's DATA port, and give the Pi its own "
                           "power supply.\n\n"
                           "The full steps are on the screen behind this.").format(
                               what=what, board=bn),
                        tr("One last step"), False, tone=_tone, button_text=_btn)
                else:
                    view = requirement_popup(
                        tr("Built. {name} is provisioned and its radio is already "
                           "in place — nothing here needs moving. Watch VITALS "
                           "for its first health beacon.").format(name=nm),
                        tr("Finished"), False, tone=_tone, button_text=_btn)
            else:
                # A WAYPOINT DOES NOT SAY "FINISHED" — NOR IN ITS TITLE
                # (operator, mid-build 2026-09-07, looking at the new blue
                # card): "at the very top it shouldn't say build finished, it
                # should say radio flashed — anything that says finished here
                # gives the illusion that we're at the end of the build."
                # Colour alone was not enough; the WORD was still claiming an
                # ending three steps into a ten-step build.
                #
                # And the standalone-RNode paragraph is wrong here too. "This
                # is a radio to plug into a phone or computer" is true of a
                # radio built on its own, and false of this one — it is going
                # onto a Raspberry Pi, which the operator was told two screens
                # ago. It stays on the standalone card, where it is true.
                if more_to_come:
                    view = requirement_popup(
                        tr("The radio is flashed and verified, and its birth "
                           "certificate is in the log below.\n\n"
                           "The build carries on behind this card."),
                        tr("Radio flashed"), False, tone=_tone, button_text=_btn)
                # A USB-verified build (T-Echo tier) demonstrated no beacon —
                # promising one on VITALS would be a claim nothing checked.
                elif getattr(getattr(self._workflow, "target", None), "verify",
                             "") == "eeprom":
                    view = requirement_popup(
                        tr("Build finished — details and the birth certificate "
                           "are in the build log below. The node was verified "
                           "over USB; when it is heard over LoRa it will appear "
                           "in VITALS."),
                        tr("Build finished"), False, tone=_tone, button_text=_btn)
                elif getattr(self, "_last_type", "") == "rnode":
                    # An RNode is a bare RADIO for a phone/computer — it does not
                    # run the mesh and never beacons, so "watch VITALS" would
                    # send the keeper looking for something that can't appear
                    # (walkthrough 2026-08-26, Tomas). Say what it's FOR.
                    view = requirement_popup(
                        tr("Build finished — the radio is flashed and verified, "
                           "and its birth certificate is in the log below.\n\n"
                           "This is a radio to plug into a phone or computer. It "
                           "won't show up in VITALS on its own — that's normal; "
                           "VITALS is for nodes that run the mesh themselves."),
                        tr("Build finished"), False, tone=_tone, button_text=_btn)
                else:
                    view = requirement_popup(
                        tr("Build finished — details and the birth certificate "
                           "are in the build log below. Watch VITALS for the "
                           "node's first health beacon."),
                        tr("Build finished"), False, tone=_tone, button_text=_btn)
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
            self.list.add_widget(_line(tr("X  Something didn't finish"), bold=True,
                                       size="18sp", color="red"))
            from ui.safety import recovery_for_board
            # The won't-flash ritual is advice for a FLASH failure only. When
            # the firmware landed and a later act failed, prescribing it sends
            # the operator to fight the wrong problem (operator, 2026-08-30:
            # "no false, no lies to the user through the UI").
            wrote_firmware = "firmware IS on the board" in " ".join(
                getattr(self, "_failure_messages", []))
            if wrote_firmware:
                self.list.add_widget(_line(tr(
                    "The firmware reached the board — a later step didn't "
                    "finish. Run it again; the medic resets the board itself, "
                    "so no buttons are needed."), size="14sp", color="amber"))
            else:
                self.list.add_widget(_line(tr(
                    "Fix the failed step above and run it again. If the board "
                    "won't flash: {recover}  If it still "
                    "won't, try a short, known-good USB data cable.").format(
                        recover=recovery_for_board(board)),
                    size="14sp", color="amber"))
            return
        self.list.add_widget(_line(tr("OK  Done!"), bold=True, size="20sp",
                                   color="green"))
        # THE HAND-OFF. On the cable path the Pi and the radio have spent the
        # whole build on Node Medic and are still there; the node does not exist
        # until they are joined. The flow used to simply END here, leaving the
        # operator holding two boards with nothing telling them what to do
        # (operator, 2026-08-02). It lives in the PANEL, not only the popup: a
        # dismissed popup is no use once your hands are full.
        if getattr(self, "_last_type", "") == "pi_rnode":
            self._handoff_block(board)
            self.list.add_widget(_line(tr("Birth another with Change at the top, "
                                          "or hit BACK."), size="13sp",
                                       color="text_secondary"))
            return
        if board is not None:
            nxt = tr("{board} is flashed & verified as an RNode on the "
                     "standard channel (915.125 / 125 / SF9 / CR5 / 17 dBm). "
                     "Unplug it and fit it to its node/Pi - it's ready to "
                     "run.").format(board=board.display_name)
        else:
            nxt = tr("Node provisioned on the standard channel. Give it power and "
                     "its antenna; it will announce and appear in VITALS as kin.")
        self.list.add_widget(_line(nxt, size="15sp"))
        self.list.add_widget(_line(tr("Birth another with Change at the top, "
                                      "or hit BACK."), size="13sp",
                                   color="text_secondary"))

    def _handoff_photos(self, board):
        """The Pi and the radio, side by side, with a joining arrow."""
        from kivy.uix.image import Image
        from ui import board_images
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(150), spacing=dp(6))
        pi_key = self._sel_pi[0] if self._sel_pi else ""
        pi_png = board_images.image_for_pi(pi_key) or ""
        board_png = board_images.image_for(getattr(board, "key", "")) or ""

        def _cell(png, caption):
            col = BoxLayout(orientation="vertical", spacing=dp(2))
            if png:
                col.add_widget(Image(source=png, allow_stretch=True,
                                     keep_ratio=True))
            col.add_widget(_line(caption, size="12.5sp",
                                 color="text_secondary"))
            return col

        row.add_widget(_cell(board_png,
                             getattr(board, "display_name", tr("the radio"))))
        arrow = _line("->", size="30sp", bold=True, color="green")
        arrow.size_hint_x = None
        arrow.width = dp(44)
        row.add_widget(arrow)
        row.add_widget(_cell(pi_png, next(
            (tr(n) for k, n in PI_HOSTS if k == pi_key),
            tr("the Raspberry Pi"))))
        return row

    def _whats_on_the_medic(self):
        """(radio_on_medic, pi_on_medic) — ASKED, not inferred.

        The old test was `_flash_radio or reached.startswith("10.55.0.")`, and
        `_flash_radio` means "this build flashed a radio SOMEWHERE", not "a
        radio is on the medic now". On the guided Pi path the flow itself says
        "Take the radio out of Node Medic" four steps before the end and even
        watches it go (_start_absence_poll) — so the flag stayed True while the
        radio sat in the operator's hand, and the closing card told them to
        unplug it again (operator, with photos, 2026-09-09).

        The radio is a question the medic can answer directly: is anything on
        its USB right now. The Pi is only on the medic if we reached it over
        the cable — a Wi-Fi address means it is powered from somewhere else.
        Fails toward "nothing attached", because inventing an unplug is the
        error that wastes the operator's time and confidence.
        """
        radio = False
        try:
            from ui.hw_factories import local_board_ports
            radio = bool(local_board_ports())
        except Exception:                                          # noqa: BLE001
            radio = False
        reached = ""
        try:
            reached = (self._pi_addr_in.text or "").strip()
        except Exception:                                          # noqa: BLE001
            reached = ""
        return radio, reached.startswith("10.55.0.")

    def _handoff_block(self, board):
        """The three physical actions that turn a finished build into a node.

        Wording comes from cable_birth.plan()/unmoved_warning() so the screen and
        the model can't drift apart, and so the radio's stable port name is
        stated where the operator is about to move it.
        """
        pi_name = tr("the Raspberry Pi")
        try:
            pi_name = next((n for k, n in PI_HOSTS
                            if self._sel_pi and k == self._sel_pi[0]), pi_name)
        except Exception:
            pass
        board_name = board.display_name if board is not None else tr("the radio")

        self.list.add_widget(_line(
            tr("Last step - join them together"), bold=True, size="17sp",
            color="accent"))
        # Show the ACTUAL two boards, not a diagram. The operator is holding
        # them; matching what's on screen to what's in their hands is the whole
        # point (standing design aim, 2026-08-02). Whichever Pi and whichever
        # radio were used, their own photos are used — and a model we have no
        # photo of simply shows no picture rather than someone else's board.
        try:
            self.list.add_widget(self._handoff_photos(board))
        except Exception:
            pass
        # SAME STATE AS THE POPUP IN FRONT OF IT. This block used to print
        # "Unplug BOTH" for every pi_rnode build with no test at all, so on the
        # already-have-a-radio road the popup correctly said "nothing here
        # needs moving" while the page underneath told the operator to unplug
        # two boards. Two opposite instructions on one screen (2026-09-09).
        radio_on, pi_on = self._whats_on_the_medic()
        if radio_on and pi_on:
            first = "1.  " + tr("Unplug both the {pi} and the {board} from "
                                "Node Medic.").format(pi=pi_name,
                                                      board=board_name)
        elif pi_on:
            first = "1.  " + tr("Unplug the {pi} from Node Medic.").format(
                pi=pi_name)
        elif radio_on:
            first = "1.  " + tr("Take the {board} off Node Medic.").format(
                board=board_name)
        else:
            first = ""          # nothing is attached; do not invent an unplug
        # PER-BOARD SOCKETS. "PWR IN" and "nearer the mini-HDMI" are Pi Zero
        # words: a 3A+ has no data micro-USB and no socket marked PWR IN, and a
        # 4B/5 has one USB-C carrying both. ui.pi_connectors exists to stop
        # exactly this and already knows the right sentence per board.
        power = ""
        try:
            from ui.pi_connectors import standalone_power_hint
            power = standalone_power_hint(
                self._sel_pi[0] if self._sel_pi else "") or ""
        except Exception:                                          # noqa: BLE001
            power = ""
        steps = [x for x in (
            first,
            f"{'2' if first else '1'}.  "
            + tr("Plug the {board} into the {pi} "
                 "with a short DATA cable.").format(board=board_name,
                                                    pi=pi_name),
            (f"{'3' if first else '2'}.  {power}" if power else
             f"{'3' if first else '2'}.  "
             + tr("Give the {pi} its own power supply.").format(pi=pi_name)),
        ) if x]
        for line in steps:
            self.list.add_widget(_line(line, size="15sp"))
        self.list.add_widget(_line(tr(
            "It starts up, finds its radio and announces itself - watch VITALS "
            "for its first health beacon."), size="14sp", color="green"))
        # Which radio, specifically. The Pi's config names one port.
        try:
            from provisioning.cable_birth import stable_port_for, unmoved_warning
            port = stable_port_for("/dev/ttyUSB0")
            self.list.add_widget(_line(unmoved_warning(port), size="13sp",
                                       color="amber"))
        except Exception:
            pass
        # Honest about what this pairing costs once the medic stops feeding it.
        try:
            from workflows.power_compat import check as _power_check
            pi_key = self._sel_pi[0] if self._sel_pi else ""
            bkey = getattr(board, "key", "")
            v = _power_check(pi_key, bkey) if (pi_key and bkey) else None
            if v and v.get("verdict") in ("blocked", "caution"):
                self.list.add_widget(_line(
                    tr("Note: Node Medic was powering the {board} during the "
                       "build. Once it runs off the {pi} the pairing's own "
                       "limits apply - {why}").format(
                           board=board_name, pi=pi_name,
                           why=v.get("why", "")), size="13sp",
                    color="amber"))
        except Exception:
            pass

    def _finish(self):
        self._mark_activity(False)               # build done -> screensaver allowed again
        self._stop_build_progress()              # build done -> ring to 100%, remove
        self._outcome_panel()
        self._popup_outcome()                    # the outcome comes TO the operator
        self._hand_back_to_guide()
        onboarding = getattr(self._workflow, "onboarding", None)
        if onboarding:
            self.list.add_widget(_line(tr("Onboarding (enter at RTNode-Setup / "
                                          "http://10.0.0.1):"), bold=True,
                                       size="16sp"))
            for k in ("node_name", "ssid", "psk", "freq", "bw", "sf", "cr",
                      "txp", "advert_en", "advert_lat", "advert_lon",
                      "advert_jitter"):
                if k not in onboarding:
                    continue
                v = onboarding.get(k, "")
                shown = v if v != "" else "____  " + tr("(operator)")
                # NEVER print the Wi-Fi password on the panel. This block is the
                # manual fallback - the values to type into the board's portal
                # by hand - and the operator is typing their OWN network's
                # password, which they already know. Showing it bought nothing
                # and put a live credential on a screen that gets photographed
                # over the operator's shoulder, and photographed BY the operator
                # to send to me (2026-09-08: "the wifi password is in plain view
                # here"). Certificates do not store it (checked: 20 on disk, 0
                # carrying a psk) - so this screen was the whole exposure.
                if k == "psk" and v:
                    shown = "\u2022" * 8 + "   " + tr("(your Wi-Fi password)")
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
        already_confirmed = "map-confirmed" in (cert.get("location") or "")
        if ll is None or already_confirmed:
            # No location, nothing to confirm — or the operator PLACED this
            # pin on the prelude map themselves (2026-08-14): it has been
            # seen by definition, and asking again at the end of a long
            # build is asking twice.
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
        # THE BOARD'S FINGERPRINT. Recognition of "a board this medic flashed"
        # reads cert["usb_serial"] in three places — and until now NOTHING wrote
        # it. So a V4 the medic had flashed itself came back as an anonymous
        # ESP32-S3 and the operator was made to pick it out of a grid of
        # look-alikes (operator, 2026-08-02). A plain RNode carries no Reticulum
        # identity, so this fingerprint is the ONLY way to know it again.
        # ...but NEVER onto a Pi's certificate (2026-09-22). A Pi build's
        # radio is not on the medic's USB, so ports[0] here is whatever
        # bystander board happens to be plugged in — and save_cert RETIRES
        # every other certificate carrying that serial, i.e. that board's
        # own. The radio's identity travels as radio_rule instead.
        if (not cert.get("usb_serial")
                and getattr(self, "_last_type", "") != "pi_rnode"):
            try:
                from ui.hw_factories import local_board_ports, LocalConnection
                from workflows.rnode_flash import usb_id_for_port
                ports = local_board_ports()
                if ports:
                    usb = usb_id_for_port(LocalConnection(), ports[0])
                    if usb:
                        cert["usb_serial"] = usb
            except Exception:
                pass                       # never block a birth on bookkeeping
        # PROVE THE RADIO BEFORE THE CERTIFICATE CLAIMS ANYTHING (#77). Birth so
        # far has proved the node is CONFIGURED; only hearing it proves it is
        # REACHABLE, and reachable is what the node is for. Runs here, with the
        # cert assembled but not yet saved, so the outcome is recorded ON it.
        # Never fatal: "configured, but I could not hear it" is a real result.
        self._add_radio_proof(cert)
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
        # THE RADIO VERDICT, said plainly and before the certificate fields —
        # an operator who is about to put this on a pole should not have to read
        # a key/value list to find out whether it was ever heard (#77).
        proof = getattr(self, "_radio_proof", None)
        if proof is not None:
            # "Not applicable" is neither good news nor bad — amber would read
            # as a problem the operator should go and fix, and there isn't one.
            if proof.not_applicable:
                mark, colour = "–  ", "text_secondary"
            elif proof.heard:
                mark, colour = "✓  ", "green"
            else:
                mark, colour = "!  ", "amber"
            self.list.add_widget(_line(mark + proof.summary, bold=True,
                                       size="15sp", color=colour))
            for c in (proof.checks or []):
                self.list.add_widget(_line("      · " + c, size="12.5sp",
                                           color="text_secondary"))
        # AND WHETHER IT WILL TELL THE MEDIC ANYTHING. Being audible and being
        # informative are different, and only the second fills VITALS — a node
        # that passes every step and then shows as a grey row for days is the
        # failure the operator has hit repeatedly (SkyFinger, 2026-08-11).
        self._add_report_verdict()
        self.list.add_widget(_line(tr("Birth certificate:"), bold=True,
                                   size="16sp"))
        self.list.add_widget(_line("    " + tr("(saved on this Node Medic)"),
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
        done = Button(text=tr("Done — back to home"), size_hint_y=None,
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

    def _add_radio_proof(self, cert):
        """Try to hear the node over the air; fold the result onto *cert* (#77).

        Best-effort by design. A node with no mesh address (a plain RNode has no
        Reticulum identity of its own) simply has nothing to hear, and that is
        not a failure of the birth — the field is left off rather than recorded
        as a failure the node could never have passed.
        """
        from workflows.radio_proof import RadioProof
        h = (cert.get("health_dst") or cert.get("reticulum_address")
             or cert.get("identity_hash") or "")
        if not h:
            # A USB-verified RTNode (the T-Echo tier) DOES have a mesh identity
            # — RNS mints one on its first configured boot — the medic just
            # never read it over USB. Saying "no mesh address of its own" about
            # a transport node whose signature was validated minutes ago is
            # false (review, 2026-08-19); the honest sentence is that the
            # address was not read, and where it will surface.
            if getattr(getattr(self, "_workflow", None), "target",
                       None) is not None and getattr(
                    self._workflow.target, "verify", "") == "eeprom":
                self._radio_proof = RadioProof.na(
                    "Not read — this node mints its own mesh identity when it "
                    "first runs configured, and the USB check does not read "
                    "it. It will announce itself over LoRa; it can be adopted "
                    "into this medic's kin when first heard.")
                cert.update(self._radio_proof.cert_fields())
                return
            # NOT APPLICABLE, said out loud (operator, 2026-08-07). A plain
            # RNode has no Reticulum identity of its own, so there is nothing
            # addressable to hear — that is not a test it failed, and it is not
            # a blank either. A blank invites the reader to guess whether the
            # check errored, was skipped, or was forgotten.
            self._radio_proof = RadioProof.na(
                "Not applicable — this node has no mesh address of its own to "
                "call, so there is nothing for the radio check to listen for.")
            cert.update(self._radio_proof.cert_fields())
            return
        # A PI+RNODE HAS NO RADIO YET, AND SAYING IT FAILED IS A LIE.
        #
        # On this path the radio is attached to the node AFTER the build — it
        # has to be, because the medic's cable holds the Pi's only USB-A until
        # then. So the check runs at a moment when the answer is guaranteed to
        # be no, and every Pi certificate ever issued has read "did not answer
        # over the radio" (Y2K8 and SolarLove, 2026-08-10). That is a false
        # negative printed on a birth certificate, which is the same offence as
        # a false positive and reads worse: the operator is told their good node
        # is deaf.
        #
        # Not applicable is the honest answer, and it is already a thing this
        # module can say.
        if getattr(self, "_last_type", "") == "pi_rnode":
            self._radio_proof = RadioProof.na(
                "Not tested yet — the radio goes onto this node after the "
                "build, so there was nothing on the air to hear. Watch the "
                "radio's own screen once it is attached and powered: it reads "
                "'On @ 1.8kbps' with a filled bar when the node has opened it.")
            cert.update(self._radio_proof.cert_fields())
            return
        try:
            from ui.app import _local_run      # LOGIN shell: rnpath is in ~/.local/bin
            from workflows.radio_proof import live_probes, prove_radio
            proof = prove_radio(h, node_name=cert.get("node_name") or "",
                                **live_probes(_local_run))
            cert.update(proof.cert_fields())
            self._radio_proof = proof
        except Exception:
            # A birth that has otherwise succeeded must not be thrown away
            # because the radio check could not run.
            self._radio_proof = None

    def _add_report_verdict(self):
        """Say, in words, what this node has actually been heard to report.

        Green ONLY when something came back. A node that answered on neither
        channel gets amber and the checks — never a quiet omission, which reads
        as "fine" to anyone skimming the page on their way out of the door.
        """
        proof = getattr(getattr(self, "_workflow", None), "report_proof", None)
        if proof is None:
            return
        mark, colour = (("✓  ", "green") if proof.anything_heard
                        else ("!  ", "amber"))
        self.list.add_widget(_line(mark + proof.summary, bold=True,
                                   size="15sp", color=colour))
        for c in (proof.checks or []):
            self.list.add_widget(_line("      · " + c, size="12.5sp",
                                       color="text_secondary"))

    def _register_kin(self, cert):
        """Record the birthed node in the medic's kin roster, stamped with
        builder = THIS medic's own unit hash — so it shows as kin, and drops to
        neighbour on VITALS/SCAN if this unit's trust is ever revoked. Best-effort."""
        try:
            from monitor import kin_roster
            from provisioning import tool_identity
            # EVERY DESTINATION THIS ONE MACHINE ANSWERS ON, recorded as ONE
            # device. A Pi propagation node has two and they are not related by
            # anything the mesh can see: rnsd announces from the node's
            # Reticulum identity, the health reporter from an identity of its
            # own. So SkyFinger — one Pi — sat in VITALS as two rows, with half
            # of what the operator wanted to know on each of them.
            #
            # Birth is the one moment anybody knows better: both hashes are in
            # the same certificate, on the machine the medic just built. The
            # health destination goes FIRST because it is the one whose beacons
            # carry the readings, and its hash becomes the device's id.
            hashes = []
            for key in ("health_dst", "reticulum_address", "identity_hash"):
                value = cert.get(key)
                if value and value not in hashes:
                    hashes.append(value)
            # THE THIRD IDENTITY OF A PI RELAY (2026-08-22). lxmd announces its
            # lxmf.propagation aspect from its OWN identity file — a hash the
            # mesh can never link to the node's rnsd or health destinations, so
            # it orphaned in VITALS as an anonymous "Propagation relay" beside
            # the same machine's named rows (the double-SkyFinger root cause).
            # Birth captured it into the certificate (workflows.build.birth_
            # certificate); record it as one more destination of THIS device so
            # its first announce folds under the node's name. extra_identities is
            # the general form for any further destinations a future role adds.
            for value in ([cert.get("lxmd_dst")]
                          + list(cert.get("extra_identities") or [])):
                if value and value not in hashes:
                    hashes.append(value)
            if not hashes:
                return
            # THE IDENTITY THE NODE ANNOUNCES UNDER, harvested from what the
            # medic has already HEARD (2026-08-14). An RTNode certificate
            # carries the health DESTINATION off the serial log, never the
            # identity behind it — yet every destination this node will ever
            # announce, including ones minted after this paperwork, carries
            # that identity, RNS-verified. If the live registry heard a cert
            # hash announce during the build, its record holds the identity;
            # writing it into the same roster device is what lets the
            # registry fold the node's OTHER destinations under this name
            # from their first announce. Best-effort: no running app or
            # nothing heard yet simply leaves the cert hashes as they were.
            try:
                from kivy.app import App
                _app = App.get_running_app()
                _reg = getattr(getattr(_app, "monitor_service", None),
                               "registry", None)
                if _reg is not None:
                    for h in list(hashes):
                        r = _reg.get(h)
                        ih = getattr(r, "identity_hash", None) if r else None
                        if ih and ih not in hashes:
                            hashes.append(ih)
            except Exception:
                pass
            # Coordinates come from the CERT — which the confirm-location gate
            # owns (moved pin -> corrected; cancelled -> removed). Reading
            # _prefill_location here bypassed that gate entirely: a rejected
            # pin still landed on the SCAN map (2026-08-01 bug hunt).
            lat = lon = None
            from ui.screens.cert_view_screen import cert_latlon
            ll = cert_latlon(cert)
            if ll:
                lat, lon = ll[0], ll[1]
            # A REBORN BOARD RETIRES ITS PREVIOUS LIVES FIRST. Same physical
            # serial + a hash the new certificate doesn't carry = an identity
            # this board used to be. Left in place, replayed announces keep the
            # old row green forever (one T114, two "live" rows, 2026-08-21).
            # Roster entry and registry rows go together; no serial, no-op.
            try:
                for _old in kin_roster.retire_previous_lives(
                        cert.get("hw_serial"), hashes):
                    if _reg is not None:
                        _reg.forget_node(_old)
            except Exception:
                pass
            _node_name = (cert.get("node_name") or cert.get("hostname")
                          or "node")
            # ONE NAME, ONE CURRENT MACHINE. The operator reuses sequential
            # names across DIFFERENT boards (a spare birthed "A2" after the
            # first was deployed). Birthing under a name a DIFFERENT device
            # already holds RETIRES that old device — a clean REPLACE, not a
            # merge (VITALS must never fold two machines by name and risk hiding
            # a dead one behind a live namesake, the 2026-08-13 hazard). A still-
            # live old board just reappears later as an anonymous neighbour.
            try:
                _replaced = kin_roster.retire_same_name(
                    _node_name, hashes, hw_serial=cert.get("hw_serial"))
                for _old in _replaced:
                    if _reg is not None:
                        _reg.forget_node(_old)
                if _replaced:
                    print(f"[kin] replaced previous node named "
                          f"{_node_name!r} ({len(_replaced)} row(s) retired)")
            except Exception:
                pass
            # THE PREDECESSOR'S IDENTITIES, FROM ITS OWN CERTIFICATES.
            # retire_previous_lives/retire_same_name consult the ROSTER - and
            # the roster can be empty (wiped 2026-08-22) while the old certs
            # still record exactly which hashes this name used to be. A
            # re-imaged machine's old identities can never speak again; only
            # replayed caches keep them moving, so they are buried too -
            # without the tombstone the path-table rediscover re-folded every
            # one of them within the hour (seen live, 2026-08-25).
            try:
                from ui import cert_store as _cs
                from monitor import tombstones as _tomb
                _dead = set(_cs.predecessor_hashes(_node_name, hashes))
                try:
                    _dead |= set(_replaced)
                except Exception:
                    pass
                if _dead:
                    for _old in _dead:
                        if _reg is not None:
                            _reg.forget_node(_old)
                    _tombs = _tomb.bury(_dead)
                    if _reg is not None:
                        _reg.set_tombstones(_tombs)
                    print(f"[kin] rebirth retired {len(_dead)} predecessor "
                          f"hash(es) for {_node_name!r}")
            except Exception as _e:
                print(f"[kin] predecessor retire skipped: {_e}")
            kin_roster.register_device(
                hashes, _node_name,
                # NOT a default — a lookup. See kin_roster.type_for_cert:
                # "rtnode2400" as the fallback labelled the first Pi propagation
                # node ever built as an RTNode-2400 and hid its wifi, bluetooth
                # and internet links (2026-08-10).
                node_type=kin_roster.type_for_cert(cert) or "rtnode2400",
                lat=lat, lon=lon,
                builder=tool_identity.identity_hash(),
                hw_serial=cert.get("hw_serial"))
            # INTO THE LIVE REGISTRY NOW, not at the next rediscover.
            # register_device writes DISK; the running registry re-reads it
            # every ~5 minutes, and a newborn node's first announce lands well
            # inside that window — on 2026-08-14 that window showed the
            # operator their own fresh nodes as grey strangers. The over-air
            # adopt path already does exactly this push.
            try:
                from kivy.app import App
                _app = App.get_running_app()
                if _app is not None:
                    _app.monitor_service.registry.set_kin_roster(
                        kin_roster.load_roster())
            except Exception:
                pass
        except Exception as e:
            print(f"[kin] register skipped: {e}")

    def _add_notes_panel(self):
        """Notes are asked HERE — after the certificate is out — then saved onto
        the stored cert (and regenerate the QR so a scan carries them too)."""
        self.list.add_widget(Widget(size_hint_y=None, height=dp(8)))
        self.list.add_widget(_line(tr("Add notes"), bold=True, size="16sp",
                                   color="accent"))
        self._end_notes_in.text = getattr(self, "_cert", {}).get("notes", "")
        self.list.add_widget(self._end_notes_in)
        save = Button(text=tr("Save notes to certificate"), size_hint_y=None,
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
        self._notes_status.text = tr("Saved. (The QR above now includes the "
                                     "notes.)")
        # refresh the QR so a fresh scan carries the notes
        self._add_cert_qr(cert)

    def set_prefill_location(self, lat, lon, source):
        """Stamp a location onto this birth (from the map's 'Use this position').
        Shown as 'Location stamped …' and folded into the certificate at the end."""
        if self._busy_with_a_build():
            self._warn_build_running()    # don't rebuild the page mid-flash
            return
        self._fresh_lap()                   # no stale board from the last lap
        self._prefill_location = (lat, lon, source)
        self._build_chooser()

    def prefill_name(self, name):
        """Seed the 'Name this node' field — used when the operator taps a node the
        medic never birthed and chooses to birth it here (from the cert viewer's
        'not birthed here' nudge)."""
        if self._busy_with_a_build():
            self._warn_build_running()    # don't rebuild the page mid-flash
            return
        self._fresh_lap()                   # no stale board from the last lap
        self._build_chooser()               # ensure the name field exists
        if getattr(self, "_name_in", None) is not None:
            self._name_in.text = str(name or "")

    def _stamp_identity(self, cert):
        """Fold the operator's node name and the map-stamped location into the
        certificate dict (notes are added at the END, after the cert is shown)."""
        name = self._name_in.text.strip()
        if name:
            cert["node_name"] = name
        # `not cert.get(...)`, NOT `"location" not in cert`. The RTNode
        # workflow ALWAYS writes "location", setting it to None when the medic
        # has no GPS fix of its own — so the key was present, this test was
        # False, and the pin the operator had just placed on the map was
        # silently dropped. Asked for it, carried it through five screens, threw
        # it away at the last step (operator, 2026-09-09: a node marked at
        # Sampleton market that arrived with location None).
        if self._prefill_location and not cert.get("location"):
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
            w = _line("    " + tr("(install 'segno' on the medic to show a "
                                  "scannable QR)"),
                      color="text_secondary", size="12sp")
            self.list.add_widget(w)
            self._qr_widgets = [w]
            return
        lbl = _line(tr("Scan to save this certificate:"), bold=True, size="15sp")
        self.list.add_widget(lbl)
        qr = QRCodeWidget(matrix)
        holder = AnchorLayout(anchor_x="center", size_hint_y=None,
                              height=qr.height + dp(12))
        holder.add_widget(qr)
        self.list.add_widget(holder)
        self._qr_widgets = [lbl, holder]
