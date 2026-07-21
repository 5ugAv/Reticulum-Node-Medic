"""On-screen keyboard — the medic's touchscreen has no physical keys.

A docked keyboard that pops up when a TextInput is focused and writes into it.
Two shapes, chosen per field:
  * ``numeric`` — a number pad (digits, decimal point, minus) for radio params
    (frequency, SF, bandwidth, dBm) and lat/lon entry.
  * ``text``    — full QWERTY with a number row, a Shift for case, and a symbols
    layer (?# / ABC) for passwords and free text.

Styling follows the operator's supplied example: rust number keys, khaki letter
keys, periwinkle-blue special keys (shift / backspace / enter / layer) on a dark
ground.

Wiring: build ONE ``OnScreenKeyboard`` at app-root level (over the ScreenManager,
so it floats above every screen) and stash it as ``app.keyboard``. Each editable
field calls :func:`bind_field` once; on focus the keyboard reveals itself and pans
the screen up so the field clears the keys.
"""

from __future__ import annotations

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button

# --- palette (from the operator's example keyboard) -----------------------
_NUM = (0.77, 0.42, 0.23, 1)      # rust / terracotta — number keys
_LET = (0.79, 0.75, 0.56, 1)      # khaki / tan       — letter & space keys
_SPEC = (0.66, 0.71, 0.87, 1)     # periwinkle blue   — shift/backspace/enter/layer
_GROUND = (0.08, 0.07, 0.06, 1)   # near-black tray
_KEYTEXT = (0.13, 0.11, 0.08, 1)  # dark glyphs on the light keys

# special key glyphs
_BKSP, _SHIFT, _ENTER, _SYM, _ABC, _SPACE = "⌫", "⇧", "↵", "?#", "ABC", "␣"

# label -> weight (relative width in its row); default 1.0
_WIDE = {_SHIFT: 1.5, _BKSP: 1.5, _SYM: 1.6, _ABC: 1.6, _ENTER: 1.6, _SPACE: 5.0}

# --- layouts (rows of key labels) -----------------------------------------
_DIGITS = list("1234567890")
_TEXT_LOWER = [
    _DIGITS,
    list("qwertyuiop"),
    list("asdfghjkl"),
    [_SHIFT] + list("zxcvbnm") + [_BKSP],
    [_SYM, ",", _SPACE, ".", _ENTER],
]
_SYMBOLS = [
    _DIGITS,
    list("@#$_&-+()/"),
    list("*\"':;!?=%"),
    [_ABC] + list("~`|[]{}") + [_BKSP],
    [_ABC, "\\", _SPACE, ".", _ENTER],
]
_NUMERIC = [
    ["7", "8", "9", _BKSP],
    ["4", "5", "6", "."],
    ["1", "2", "3", "-"],
    ["0", _ENTER],
]

# labels that are letters (get upper-cased when Shift is on)
def _is_letter(label):
    return len(label) == 1 and label.isalpha()


class OnScreenKeyboard(BoxLayout):
    """A docked touchscreen keyboard. ``pan_target`` (the ScreenManager) is slid
    up when needed so the focused field stays visible above the keys."""

    def __init__(self, pan_target=None, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None, height=0,
                         padding=dp(6), spacing=dp(5), opacity=0, **kwargs)
        self._pan_target = pan_target
        self.target = None            # the TextInput being edited
        self._layer = "text"          # 'text' | 'symbols' | 'numeric'
        self._shift = False
        self._hidden = True
        self._applied_shift = 0        # current pan applied to the ScreenManager
        with self.canvas.before:
            self._bg = Color(*_GROUND)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync_bg, size=self._sync_bg)

    def _sync_bg(self, *_):
        self._rect.pos, self._rect.size = self.pos, self.size

    # -- public API ---------------------------------------------------------

    def show(self, target, numeric=False):
        """Reveal the keyboard for ``target``. Re-showing the same visible field
        keeps the current layer (so a Shift/symbols choice survives key taps)."""
        new_target = target is not self.target
        self.target = target
        if self._hidden or new_target:
            self._shift = False
            self._layer = "numeric" if numeric else "text"
            self._numeric = numeric
            self._build()
            self._reveal()
        self._pan_for(target)

    def hide(self, *_):
        self._hidden = True
        self.height, self.opacity, self.disabled = 0, 0, True
        self._restore_pan()
        self.target = None

    # -- build --------------------------------------------------------------

    def _rows(self):
        if self._layer == "numeric":
            return _NUMERIC
        if self._layer == "symbols":
            return _SYMBOLS
        return _TEXT_LOWER

    def _build(self):
        self.clear_widgets()
        for row in self._rows():
            rb = BoxLayout(orientation="horizontal", spacing=dp(5),
                           size_hint_y=1)
            for label in row:
                rb.add_widget(self._key(label))
            self.add_widget(rb)

    def _key(self, label):
        if label in (_SHIFT, _BKSP, _ENTER, _SYM, _ABC):
            fill = _SPEC
        elif label == _SPACE:
            fill = _LET
        elif label in _DIGITS:
            fill = _NUM
        elif _is_letter(label):
            fill = _LET
        else:                                   # symbols / punctuation
            fill = _LET
        shown = label
        if _is_letter(label) and self._shift:
            shown = label.upper()
        if label == _SHIFT:
            shown = _SHIFT + ("●" if self._shift else "")
        if label == _SPACE:
            shown = ""                          # a plain wide bar reads as space
        b = Button(text=shown, font_size="19sp", bold=True,
                   background_normal="", background_down="",
                   background_color=fill, color=_KEYTEXT,
                   size_hint_x=_WIDE.get(label, 1.0))
        b.bind(on_release=lambda _b, lbl=label: self._on_key(lbl))
        return b

    # -- key handling -------------------------------------------------------

    def _on_key(self, label):
        t = self.target
        if label == _BKSP:
            if t:
                t.do_backspace()
        elif label == _ENTER:
            if t and getattr(t, "multiline", False):
                t.insert_text("\n")
            else:
                self.hide()
                return
        elif label == _SHIFT:
            self._shift = not self._shift
            self._build()
        elif label == _SYM:
            self._layer, self._shift = "symbols", False
            self._build()
        elif label == _ABC:
            self._layer, self._shift = "text", False
            self._build()
        elif label == _SPACE:
            if t:
                t.insert_text(" ")
        else:
            ch = label.upper() if (_is_letter(label) and self._shift) else label
            if t:
                t.insert_text(ch)
            if self._shift and _is_letter(label):   # one-shot shift, like a phone
                self._shift = False
                self._build()
        self._refocus()

    def _refocus(self):
        # tapping a key blurred the field; put the caret back next frame
        t = self.target
        if t is not None:
            Clock.schedule_once(lambda dt: setattr(t, "focus", True), 0)

    # -- reveal + pan -------------------------------------------------------

    def _reveal(self):
        rows = len(self._rows())
        self.height = dp(56) * rows + dp(12)
        self.opacity, self.disabled, self._hidden = 1, False, False

    def _pan_for(self, target):
        sm = self._pan_target
        if sm is None or target is None:
            return
        try:
            _, wy = target.to_window(target.x, target.y)
        except Exception:
            return
        # wy already includes any pan we've applied — subtract it to get the
        # field's UNPANNED position, so recomputing on each keystroke is stable
        # (else the second read cancels the first and the field drops behind the
        # keys). The keyboard is docked at the bottom, so its top is at self.height.
        natural_wy = wy - self._applied_shift
        overlap = (self.height + dp(14)) - natural_wy
        self._set_pan(max(0, overlap))

    def _set_pan(self, shift):
        sm = self._pan_target
        if sm is None:
            return
        if shift <= 0:
            self._restore_pan()
            return
        sm.size_hint_y = None
        sm.height = sm.parent.height if sm.parent else Window.height
        sm.y = shift
        self._applied_shift = shift

    def _restore_pan(self):
        self._applied_shift = 0
        sm = self._pan_target
        if sm is None:
            return
        sm.y = 0
        sm.size_hint_y = 1


def bind_field(text_input, numeric=False):
    """Make ``text_input`` summon the app keyboard on focus. ``numeric=True`` gives
    the number pad (digits/decimal/minus); otherwise the full text keyboard."""
    def _on_focus(inst, focused):
        if not focused:
            return
        app = App.get_running_app()
        kb = getattr(app, "keyboard", None)
        if kb is not None:
            kb.show(inst, numeric=numeric)
    text_input.bind(focus=_on_focus)
    text_input._rnm_kb_numeric = numeric
    return text_input
