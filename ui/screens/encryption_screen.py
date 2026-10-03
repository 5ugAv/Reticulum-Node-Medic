"""Settings ▸ Encrypt my records — the switch that turns it on and off.

A shell over ``provisioning.encryption_flow``; every decision lives there, so
it is tested without a display. Nothing in this suite instantiates Kivy, and
logic left in a screen is logic nothing checks — the setup wizard shipped a
TypeError on its first screen with a green suite (2026-08-31).

THE SHAPE. One overview, then one door per screen, then the work.

THIS IS WHERE THE KEYS ARE SET, not re-entered. The setup wizard collects a
passphrase and a recovery key and stores NEITHER — correctly, since a stored
passphrase is a passphrase on the card. So the first time any of them becomes a
real key is here, and every one is set the way the wizard sets one: typed twice,
or generated and copied back. A typo here is permanent, and nothing on the medic
can check it against what the operator meant.
"""
from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from provisioning import encryption_flow as ef
from provisioning import records_vault as rv
from provisioning import vault_factors as vf
from ui import theme
from ui.widgets.pattern_pad import PatternPad


def _line(text, size="15sp", color="text_primary", bold=False):
    """A label that grows to its text instead of clipping it.

    Fixed heights here have cut the top off a screen twice (the parts list, the
    step headers). Every label on this screen sizes to its texture.
    """
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="top", size_hint_y=None,
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
             texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(6)))
    return lbl


def _button(text, on_press, color="accent"):
    b = Button(text=text, size_hint_y=None, height=dp(62), font_size="18sp",
               bold=True, background_normal="", background_down="",
               background_color=theme.hex_to_rgba(theme.COLORS[color]),
               # dark ink on the bright key — text_primary on accent was two
               # greens a shade apart, invisible on the glass (walk 2026-09-29)
               color=theme.hex_to_rgba(theme.COLORS["background"]))
    b.bind(on_release=lambda *_: on_press())
    return b


class EncryptionScreen(BoxLayout):
    """Turn encryption on or off, proving each door on the way in."""

    def __init__(self, home=None, on_done=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        kwargs.setdefault("padding", dp(18))
        kwargs.setdefault("spacing", dp(10))
        super().__init__(**kwargs)
        self._home = home
        self._on_done = on_done
        self._busy = False
        # PatternPad is a live widget holding a callback into this screen, and
        # a touch that lands after the operator has moved on would reach
        # _pattern_drawn with no stage set. Initialised here so that is a
        # no-op rather than an AttributeError mid-gesture.
        self._pattern_first = None
        self._asking = None
        self._parts = {}
        self._body = BoxLayout(orientation="vertical", size_hint_y=None,
                               spacing=dp(10))
        self._body.bind(minimum_height=self._body.setter("height"))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        scroll.add_widget(self._body)
        self.add_widget(scroll)
        self.show_overview()

    # -- helpers ------------------------------------------------------------

    def _stage(self, *widgets):
        self._body.clear_widgets()
        for w in widgets:
            self._body.add_widget(w)

    @staticmethod
    def _keyboard(field):
        """The panel has no physical keys; a field that does not raise the
        on-screen keypad cannot be filled in."""
        try:
            from ui.onscreen_keyboard import bind_field
            bind_field(field)
        except Exception:
            pass

    # -- overview -----------------------------------------------------------

    def show_overview(self):
        st = ef.state(self._home)
        rows = [_line(ef.headline(st), "19sp",
                      color="green" if st["on"] else "warning_yellow", bold=True)]

        for ok, text in st["covered"]:
            rows.append(_line(("ENCRYPTED  " if ok else "NOT ENCRYPTED  ") + text,
                              "14sp", color="text_secondary" if ok else "amber"))

        if st["blockers"]:
            for b in st["blockers"]:
                rows.append(_line(b, "15sp", color="warning_yellow"))
        elif st["on"]:
            rows.append(_line(
                "Turning it off decrypts every file and removes the keyring. "
                "You will need one of your keys to do it.", "14sp",
                color="text_secondary"))
            rows.append(_button("Turn encryption off", self._begin_off,
                                color="surface"))
        else:
            rows.append(_line(
                "You will set three keys, and any one of them opens your "
                "records: your daily unlock, your passphrase, and a recovery "
                "key to write down. Nothing is encrypted until all three are "
                "set, and you can turn this off again at any time.", "14sp",
                color="text_secondary"))
            rows.append(_button("Turn encryption on", self._begin_on))

        rows.append(Widget(size_hint_y=None, height=dp(8)))
        self._stage(*rows)

    # -- collecting the doors ------------------------------------------------

    def _begin_on(self):
        self._policy = vf.load_policy()
        self._todo = ef.doors_to_set(self._policy)
        self._parts = {}
        self._passphrase = ""
        self._recovery = ""
        self._ask_next()

    def _ask_next(self):
        if not self._todo:
            return self._run_on()
        self._asking = self._todo.pop(0)
        kind = self._asking["kind"]
        if kind == "pattern":
            self._pattern_first = None
            self._ask_pattern()
        elif kind == "keyfile":
            self._ask_keyfile()
        elif kind == "recovery":
            self._show_recovery_key()
        else:
            self._ask_passphrase()

    def _ask_pattern(self):
        again = self._pattern_first is not None
        self._status = _line("", "14sp", color="amber")
        pad = PatternPad(on_complete=self._pattern_drawn)
        self._stage(
            _line("Draw it again to confirm." if again
                  else self._asking["prompt"], "19sp", bold=True),
            _line("A pattern is drawn, not read back — there is nothing on "
                  "screen afterwards to check it against, so you draw it twice."
                  if not again else "Exactly as you drew it a moment ago.",
                  "14sp", color="text_secondary"),
            self._status, pad,
            _button("Cancel", self.show_overview, color="surface"))

    def _pattern_drawn(self, path):
        """Twice, and they must agree. This pattern BECOMES the key — a slip
        while setting it produces a vault whose key is a gesture nobody ever
        made deliberately, found out at the worst possible moment."""
        if not self._asking or self._asking.get("kind") != "pattern":
            return                               # a stray touch after moving on
        try:
            if self._pattern_first is None:
                vf.encode_pattern(path)          # raises if too short
                self._pattern_first = list(path)
                return self._ask_pattern()
            self._parts[vf.PATTERN] = vf.confirm_pattern(self._pattern_first,
                                                         path)
        except vf.FactorError as exc:
            self._pattern_first = None
            self._ask_pattern()
            self._status.text = str(exc)
            return
        self._ask_next()

    def _ask_keyfile(self):
        """Reading the stick needs the same code the unlock screen uses. Until
        that is shared, say so rather than pretend."""
        self._stage(
            _line("USB key unlock is not wired to this switch yet.", "19sp",
                  bold=True, color="warning_yellow"),
            _line("Your daily unlock uses a USB key, and turning encryption on "
                  "from here cannot read it yet. Nothing has been changed.",
                  "15sp", color="text_secondary"),
            _button("Back", self.show_overview, color="surface"))

    def _text_field(self, hint=""):
        f = TextInput(multiline=False, password=True, hint_text=hint,
                      size_hint_y=None, height=dp(58), font_size="24sp",
                      write_tab=False)
        self._keyboard(f)
        return f

    def _ask_passphrase(self):
        """Typed twice. A passphrase set with a typo is a vault nobody can
        open, and unlike the wizard there is no later step that catches it."""
        first = self._text_field("your passphrase")
        again = self._text_field("the same again")
        self._status = _line("", "14sp", color="amber")

        def go():
            text = first.text or ""
            problem = (ef.passphrase_problem(text)
                       or ef.confirm_problem(text, again.text or ""))
            if problem:
                self._status.text = problem
                return
            if self._asking["door"] == "daily":
                self._parts[vf.PASSPHRASE] = text
            self._passphrase = text
            self._ask_next()

        self._stage(_line(self._asking["prompt"], "19sp", bold=True),
                    _line(self._asking.get("detail", ""), "14sp",
                          color="text_secondary"),
                    self._status, first, again, _button("Next", go),
                    _button("Cancel", self.show_overview, color="surface"))
        Clock.schedule_once(lambda _dt: setattr(first, "focus", True), 0.3)

    def _show_recovery_key(self):
        """Generated, shown, and copied back. It is the only door the operator
        cannot choose, so the medic has to be sure it left the screen."""
        if not getattr(self, "_recovery", None):
            self._recovery = ef.new_recovery_key()   # generated once; shown as often as asked
        # NAME THE MEDIC on this screen. Operator, 2026-09-01: an owner with
        # several medics photographs these keys to keep them, and a photo of a
        # bare key does not say which unit it opens.
        try:
            from provisioning.tool_identity import tool_name
            name = tool_name()
        except Exception:
            name = ""
        head = f"{name} — recovery key" if name else "Your recovery key"
        self._stage(
            _line(head, "19sp", bold=True),
            _line("Write this on paper and keep it away from the medic. It is "
                  "shown once.", "14sp", color="amber"),
            _line(self._recovery, "26sp", color="accent", bold=True),
            _line(self._asking.get("detail", ""), "14sp", color="text_secondary"),
            _button("I have written it down", self._ask_recovery_back),
            _button("Cancel", self.show_overview, color="surface"))

    def _ask_recovery_back(self):
        field = self._text_field("type the key")
        field.password = False
        self._status = _line("", "14sp", color="amber")

        def go():
            typed = field.text or ""
            problem = ef.recovery_problem(typed)
            if problem:
                self._status.text = problem
                return
            if not ef.recovery_matches(self._recovery, typed):
                self._status.text = ("That is not the key on the last screen. "
                                     "Check what you wrote down.")
                return
            self._ask_next()

        self._stage(
            _line("Type it back.", "19sp", bold=True),
            _line("Read it off the paper, not the screen — that is what proves "
                  "you can do it later.", "14sp", color="text_secondary"),
            self._status, field, _button("Next", go),
            _button("Show me the key again", self._show_recovery_key,
                    color="surface"))
        Clock.schedule_once(lambda _dt: setattr(field, "focus", True), 0.3)

    # -- doing the work ------------------------------------------------------

    def _begin_off(self):
        field = TextInput(multiline=False, password=True, size_hint_y=None,
                          height=dp(58), font_size="24sp", write_tab=False)
        self._keyboard(field)
        self._status = _line("", "14sp", color="amber")

        def go():
            if not (field.text or ""):
                self._status.text = "Type one of your keys."
                return
            self._work("Decrypting your records…",
                       lambda: ef.turn_off(field.text, home=self._home))

        self._stage(
            _line("Type any one of your keys.", "19sp", bold=True),
            _line("Your passphrase, or your recovery key. Whichever you have.",
                  "14sp", color="text_secondary"),
            self._status, field, _button("Turn encryption off", go),
            _button("Cancel", self.show_overview, color="surface"))
        Clock.schedule_once(lambda _dt: setattr(field, "focus", True), 0.3)

    def _run_on(self):
        self._work("Encrypting your records…", lambda: ef.turn_on(
            self._parts, self._passphrase, self._recovery,
            home=self._home, policy=self._policy))

    def _work(self, message, fn):
        """Run *fn* off the UI thread. Each scrypt derivation is ~1.6s on the
        Pi 5 and there are several; on the main thread the screen would freeze
        and read as a crash."""
        if self._busy:
            return
        self._busy = True
        self._stage(_line(message, "19sp", bold=True),
                    _line("This takes a few seconds. Do not power the medic "
                          "off.", "15sp", color="amber"))

        def run():
            try:
                res = fn()
                Clock.schedule_once(lambda _dt: self._finished(res, None), 0)
            except Exception as exc:
                Clock.schedule_once(lambda _dt, e=exc: self._finished(None, e), 0)

        threading.Thread(target=run, daemon=True).start()

    def _finished(self, result, error):
        self._busy = False
        if error is not None:
            self._stage(
                _line("That did not work.", "19sp", bold=True,
                      color="warning_yellow"),
                _line(str(error), "15sp", color="text_secondary"),
                _line("Nothing was left half-done — your records are as they "
                      "were.", "14sp", color="text_secondary"),
                _button("Back", self.show_overview, color="surface"))
            return
        n = result.get("files", 0) if isinstance(result, dict) else 0
        self._stage(
            _line("Done.", "19sp", bold=True, color="green"),
            _line(f"{n} file{'' if n == 1 else 's'} changed.", "15sp",
                  color="text_secondary"),
            _button("Back", self.show_overview, color="surface"))
        if self._on_done:
            try:
                self._on_done()
            except Exception:
                pass
