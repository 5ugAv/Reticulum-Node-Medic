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
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from node_profile import RadioConfig
from ui import theme
from ui.birth import birth_node_types, rnode_board_choices
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
                 on_mitosis=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.padding = dp(12)
        self.spacing = dp(8)
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
        self._board_by_name = {b.display_name: b for b in self._boards}
        self._pi_by_name = {name: key for key, name in PI_HOSTS}

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

    def _build_chooser(self):
        """Two dropdowns — flashable RNode board (left) + host Pi (right) — then
        Continue. RTNode-2400 and Mitosis stay as secondary entry points."""
        self.header.clear_widgets()
        self.list.clear_widgets() if hasattr(self, "list") else None
        self.header.add_widget(_line("Birth a new node", bold=True, size="22sp"))
        self.header.add_widget(_line("Choose the radio board and the host Pi:",
                                     size="13sp", color="text_secondary"))

        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(82),
                        spacing=dp(10))
        left = BoxLayout(orientation="vertical", spacing=dp(4))
        left.add_widget(_line("Radio board (RNode)", size="13sp", color="accent"))
        self._board_spinner = Spinner(
            text="Select a board", values=[b.display_name for b in self._boards],
            size_hint_y=None, height=dp(52), font_size="15sp",
            background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        left.add_widget(self._board_spinner)
        right = BoxLayout(orientation="vertical", spacing=dp(4))
        right.add_widget(_line("Host Pi", size="13sp", color="accent"))
        self._pi_spinner = Spinner(
            text="Select a Pi", values=[name for _, name in PI_HOSTS],
            size_hint_y=None, height=dp(52), font_size="15sp",
            background_normal="",
            background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
            color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        right.add_widget(self._pi_spinner)
        row.add_widget(left)
        row.add_widget(right)
        self.header.add_widget(row)

        cont = Button(text="Continue", size_hint_y=None, height=dp(56),
                      font_size="20sp", bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["accent"]),
                      color=theme.hex_to_rgba(theme.COLORS["background"]))
        cont.bind(on_release=lambda *_: self._on_continue())
        self.header.add_widget(cont)

        extra = BoxLayout(orientation="horizontal", size_hint_y=None,
                          height=dp(40), spacing=dp(8))
        rt = Button(text="RTNode-2400 (ESP32, no Pi)", font_size="12sp",
                    background_normal="",
                    background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                    color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        rt.bind(on_release=lambda *_: self.show_params("rtnode2400"))
        mit = Button(text="Mitosis (clone tool)", font_size="12sp",
                     background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        mit.bind(on_release=lambda *_: self._on_mitosis and self._on_mitosis())
        extra.add_widget(rt)
        extra.add_widget(mit)
        self.header.add_widget(extra)

    def _on_continue(self):
        """Validate the board+Pi selection, warn on a power-incompatible combo,
        else go to the pre-filled params form."""
        board = self._board_by_name.get(self._board_spinner.text)
        if board is None:
            self.list.clear_widgets()
            self.list.add_widget(_line("Pick a radio board first.", color="amber"))
            return
        pi_key = self._pi_by_name.get(self._pi_spinner.text, "none")
        if pi_key != "none":
            verdict = power_check(pi_key, board.key)
            if verdict and verdict.get("verdict") in ("blocked", "caution"):
                self._show_power_popup(verdict, board.display_name, pi_key,
                                       lambda: self._proceed(board, pi_key))
                return
        self._proceed(board, pi_key)

    def _proceed(self, board, pi_key):
        node_type = "rnode" if pi_key == "none" else "pi_rnode"
        self.show_params(node_type, board=board)

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
        d = RadioConfig()                                 # canonical defaults
        if board is not None:
            self.list.add_widget(_line(f"{board.display_name}", bold=True,
                                       size="16sp"))
        self.list.add_widget(_line(
            "Radio settings — pre-filled with our standard config. Change only "
            "if you know why, then press OK.", size="14sp"))
        fields = [
            ("freq", "Frequency (MHz)", f"{d.frequency_mhz:g}"),
            ("bw", "Bandwidth (kHz)", f"{d.bandwidth_khz:g}"),
            ("sf", "Spreading factor", str(d.spreading_factor)),
            ("cr", "Coding rate", str(d.coding_rate)),
            ("txp", "TX power (dBm)", str(d.tx_power_dbm)),
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
        self._param_inputs[key] = ti
        row.add_widget(ti)
        return row

    def _read_params(self):
        d = RadioConfig()

        def num(key, cast, default):
            try:
                return cast(self._param_inputs[key].text.strip())
            except (ValueError, KeyError):
                return default            # blank/garbage falls back to canonical

        return {
            "freq": num("freq", float, d.frequency_mhz),
            "bw": num("bw", float, d.bandwidth_khz),
            "sf": num("sf", int, d.spreading_factor),
            "cr": num("cr", int, d.coding_rate),
            "txp": num("txp", int, d.tx_power_dbm),
        }

    def _apply_radio(self, workflow, radio):
        r = getattr(getattr(workflow, "profile", None), "radio", None)
        if r is None:
            return
        r.frequency_mhz = radio["freq"]
        r.bandwidth_khz = radio["bw"]
        r.spreading_factor = radio["sf"]
        r.coding_rate = radio["cr"]
        r.tx_power_dbm = radio["txp"]

    def _confirm_params(self, node_type, board):
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
        self._launch(workflow, title)

    def _launch(self, workflow, title):
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
            self.list.add_widget(_line("Birth certificate:", bold=True,
                                       size="16sp"))
            for k, v in cert.items():
                self.list.add_widget(_line(f"    {k}: {v}", size="13sp"))
            self._add_cert_qr(cert)

    def _add_cert_qr(self, cert):
        """Show the certificate as a scannable QR — the medic has no phone
        tethered, so this is how the operator gets it off the device: scan with
        any camera, no pairing or network. Falls back to a hint if segno is
        absent (the text above is still the record)."""
        matrix = qr_matrix(birth_cert_payload(cert))
        if not matrix:
            self.list.add_widget(_line(
                "    (install 'segno' on the medic to show a scannable QR)",
                color="text_secondary", size="12sp"))
            return
        self.list.add_widget(_line("Scan to save this certificate:", bold=True,
                                   size="15sp"))
        qr = QRCodeWidget(matrix)
        holder = AnchorLayout(anchor_x="center", size_hint_y=None,
                              height=qr.height + dp(12))
        holder.add_widget(qr)
        self.list.add_widget(holder)
