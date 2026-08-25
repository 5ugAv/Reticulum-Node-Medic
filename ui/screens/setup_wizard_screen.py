"""First-use setup — one instruction per screen, security first, then the tour.

The ORDER and the RULES are pure data in ``ui.setup_flow``; this file is the
presentation over them, exactly as ``ui.screens.birth_guide_screen`` is over
``ui.birth_guide_flow``. Nothing here decides whether a step may be entered —
it asks ``blocked_reason`` and shows what it says. A gate re-implemented in a
widget is a gate that stops matching the one in the tests.

WHAT THIS SCREEN CAN AND CANNOT DO, said plainly because the summary at the end
depends on it. It collects the operator's choices, verifies each one against the
thing that can verify it (the recovery key by typing it back, the pattern by
drawing it twice, the USB key by reading the bytes off the stick), and records
the chosen policy. It does NOT create the encrypted container: that work is
``provisioning.vault``'s, it has never been run on a real medic, and it needs a
boot-unlock decision that has not been made ([[encrypt-at-rest]], task #34). So
``enrol_fn`` is INJECTED — the same shape as ``VaultUnlockScreen``'s
``unlock_fn`` — and when nobody supplies one the summary says outright that no
container exists rather than congratulating the operator on records that are
sitting in the clear.

THE SECRETS DO NOT LIVE ON THIS OBJECT. The passphrase, the recovery key and the
pattern are held only long enough to hand to ``enrol_fn``, and cleared the
moment the security half finishes. A wizard left on the screen stack with a
passphrase in an attribute is a passphrase in a core dump.
"""

from __future__ import annotations

import os
import threading

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

from ui import setup_flow as sf
from ui import theme
from ui.i18n import tr  # i18n: wrapped — nav chrome only; see ui/setup_flow.py
from ui.widgets.pattern_pad import PatternPad
from ui.widgets.wizard_step import WizardStep
from provisioning import first_use, recovery_key, usb_key
from provisioning.vault_factors import (KEYFILE, PASSPHRASE, PATTERN,
                                        FactorError, confirm_pattern, describe,
                                        encode_pattern, keyfile_secret,
                                        level_name, save_policy)

#: The front-page artwork the tour crops its cards out of.
POSTER = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "front_page.png"))

#: How often the USB step looks for a stick. Slow enough not to hammer /media,
#: fast enough that plugging one in feels like the medic noticed.
STICK_POLL_S = 1.5


def _grow(text, size="15sp", color="text_secondary", bold=False):
    """A label whose height FOLLOWS its wrapped text.

    Same fix as the guided birth's hint (photo, 2026-08-09): a pinned height
    fits the sentence it was measured against and draws the next one straight
    through the paragraph above. Every variable-length string on these screens —
    the honest per-level descriptions, the summary lines, a refusal — is one
    that will get longer.
    """
    lbl = Label(text=text, font_size=theme.font_sp(size), bold=bold,
                halign="left", valign="top", size_hint_y=None,
                color=theme.hex_to_rgba(theme.COLORS[color]))
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
             texture_size=lambda i, ts: setattr(i, "height", ts[1]))
    return lbl


def _board_image_widget(board_key: str):
    """An Image of a specific board (e.g. the Heltec Tracker) for a tour step,
    or None. [[show-dont-tell-ux]]: when the medic knows exactly which board a
    step is about, show that board, not a generic card. Best-effort and silent
    on a missing asset — the words still work."""
    try:
        from kivy.uix.image import Image as UIImage
        from ui.board_images import image_for
        path = image_for(board_key)
        if not path or not os.path.exists(path):
            return None
        img = UIImage(source=path, size_hint_y=None, height=dp(150),
                      allow_stretch=True, keep_ratio=True)
        return img
    except Exception:                  # noqa: BLE001
        return None


def poster_card_image(zone: str):
    """An Image of one painted front-page card, or None.

    [[show-dont-tell-ux]]: the tour shows the operator the card they are about
    to press rather than naming it. Cropped out of the poster itself via
    ``home_zones.card_rect``, so a card that moves in the artwork moves here
    with it.

    Best-effort and silent on failure. A missing or unreadable poster must cost
    a picture, never the screen — the words alone still work, and a setup
    walkthrough that crashes because an asset is absent is a medic that cannot
    be set up.
    """
    try:
        from kivy.core.image import Image as CoreImage
        from kivy.uix.image import Image as UIImage
        from ui.home_zones import card_rect
        rect = card_rect(zone)
        if rect is None or not os.path.exists(POSTER):
            return None
        left, top, w, h = rect
        tex = CoreImage(POSTER).texture
        tw, th = tex.size
        # card_rect measures DOWNWARD from the top; texture y grows upward.
        region = tex.get_region(int(left * tw), int((1.0 - top - h) * th),
                                max(1, int(w * tw)), max(1, int(h * th)))
        img = UIImage(texture=region, allow_stretch=True, keep_ratio=True)
        return img
    except Exception:
        return None


class SetupWizardScreen(BoxLayout):
    """The first-use walkthrough.

    ``on_finish()`` fires when the operator reaches the end (or leaves for the
    front page); ``on_navigate(screen)`` opens a mode from a tour screen.
    ``enrol_fn(parts, policy, recovery_key) -> (ok, message)`` does the real
    vault work when there is any to do — see the module docstring for why it is
    injected. ``vault_exists_fn()`` answers the disk for the summary.
    """

    def __init__(self, on_finish=None, on_navigate=None, enrol_fn=None,
                 vault_exists_fn=None, marker_path=None,
                 mount_lister=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self._on_finish = on_finish
        self._on_navigate = on_navigate
        self._enrol_fn = enrol_fn
        self._vault_exists_fn = vault_exists_fn or (lambda: False)
        self._marker_path = marker_path or first_use.MARKER_PATH
        self._mount_lister = mount_lister
        self.reset()

    # -- lifecycle ---------------------------------------------------------

    def reset(self):
        """Start the walkthrough from nothing.

        Called on every entry, including a re-run from Settings. A wizard that
        carried the last run's state would show the next operator — the one this
        medic was handed to — a summary of somebody else's choices.
        """
        self._stop_stick_poll()
        self._state = sf.SetupState()
        self._i = 0
        self._current = None
        self._forget_secrets()
        self._pattern_first = None
        self._stick_dirs = []
        self._notice = ""
        # The enrolment runs once, at the summary. Cleared here so a re-run from
        # Settings — a medic being handed to somebody else — applies the NEW
        # operator's choices instead of reporting the last one's as already done.
        self._applied = False
        self._applied_msg = ""
        self._stick_status = None
        self._render()

    def _forget_secrets(self):
        """Drop everything the operator typed.

        Held only between the screen that collects a factor and the hand-off
        that uses it. A wizard sitting on the screen stack with a passphrase in
        an attribute is a passphrase in a core dump, and this screen stays
        alive for the whole session once Settings can re-open it.
        """
        self._passphrase = ""
        self._recovery = ""
        self._pattern = ""
        self._keyfile_secret = ""

    # -- navigation --------------------------------------------------------

    def _steps(self):
        return sf.setup_steps(self._state)

    def _step(self):
        steps = self._steps()
        self._i = max(0, min(self._i, len(steps) - 1))
        return steps[self._i]

    def _next(self):
        steps = self._steps()
        if self._i >= len(steps) - 1:
            self._finish()
            return
        self._goto(self._i + 1)

    def _back(self):
        if self._i <= 0:
            self._finish(skipped=True)
            return
        self._goto(self._i - 1)

    def _goto(self, index):
        """Move to a step, refusing one whose precondition has gone.

        The refusal is SHOWN, not swallowed. A wizard that silently stayed put
        looks like a dead button, which is the single hardest fault for an
        operator to report and the one they blame themselves for.
        """
        steps = self._steps()
        index = max(0, min(index, len(steps) - 1))
        key = steps[index]["key"]
        why = sf.blocked_reason(key, self._state)
        if why:
            self._notice = why
            self._render()
            return
        self._notice = ""
        self._i = index
        self._render()

    def _finish(self, skipped=None, hand_back=True):
        """Record that the walkthrough ran, forget the secrets, and hand back.

        The marker is written HERE and nowhere earlier. Writing it as each step
        completed would mean a medic closed halfway through recorded itself as
        set up, and the operator would never be offered the rest.

        *hand_back* is False when the operator is leaving for a MODE rather than
        for the front page. Sending them home first and then on to BIRTH would
        run two transitions in opposite directions for one tap, and the second
        would read as the medic changing its mind.
        """
        if skipped is None:
            skipped = not self._state.factors_ready
        try:
            first_use.mark_completed(security_skipped=bool(skipped),
                                     path=self._marker_path)
        except Exception:
            pass
        self._stop_stick_poll()
        self._forget_secrets()
        if hand_back and self._on_finish:
            self._on_finish()

    # -- rendering ---------------------------------------------------------

    def _render(self):
        self._stop_current()
        self.clear_widgets()
        step = self._step()
        renderer = {
            sf.RECOVERY_KEY: self._render_recovery_key,
            sf.RECOVERY_KEY_BACK: self._render_type_it_back,
            sf.PASSPHRASE_STEP: self._render_passphrase,
            sf.LEVEL: self._render_level,
            sf.PATTERN_STEP: self._render_pattern,
            sf.KEYFILE_STEP: self._render_keyfile,
            sf.SECURITY_SUMMARY: self._render_summary,
        }.get(step["key"], self._render_plain)
        renderer(step)

    def _stop_current(self):
        self._stop_stick_poll()
        cur = getattr(self, "_current", None)
        if cur is not None and hasattr(cur, "stop"):
            try:
                cur.stop()
            except Exception:
                pass
        self._current = None

    def _wizard(self, step, anim=None, on_next=None, next_text=None,
                input_widget=None):
        """A WizardStep for *step*, with this flow's shared decisions applied.

        In one place so they cannot drift apart between nine renderers: the
        counter counts THIS operator's steps (the list shrinks when a level
        without a pattern is chosen), a self-advancing step shows no Next, and
        every step keeps its Back.
        """
        steps = self._steps()
        w = WizardStep(index=self._i, total=len(steps), title=step["title"],
                       body=step["body"], anim=anim,
                       hint=step.get("hint", ""),
                       warning=step.get("warning", ""),
                       input_widget=input_widget,
                       next_text=next_text or step.get("next"),
                       on_next=on_next or self._next, on_back=self._back)
        if step.get("self_advancing"):
            w.hide_next()
        self.add_widget(w)
        self._current = w
        if hasattr(w, "start"):
            w.start()
        return w

    def _notice_widget(self):
        """The refusal from the last blocked move, or None.

        Rendered above the step's own words so it reads as an answer to what
        the operator just did, not as part of the instruction.
        """
        if not self._notice:
            return None
        lbl = _grow(self._notice, "14sp", color="warning_yellow", bold=True)
        return lbl

    def _render_plain(self, step):
        """A talking step — welcome, what-is-locked, or one screen of the tour."""
        stage = BoxLayout(orientation="vertical", spacing=dp(8))
        note = self._notice_widget()
        if note is not None:
            stage.add_widget(note)
        card = step.get("poster_card")
        if card:
            img = poster_card_image(card)
            if img is not None:
                stage.add_widget(img)
        board = step.get("board_image")
        if board:
            bimg = _board_image_widget(board)
            if bimg is not None:
                stage.add_widget(bimg)
        if step.get("opens"):
            stage.add_widget(Widget())
            stage.add_widget(self._see_it_button(step))
        self._wizard(step, anim=stage)

    def _see_it_button(self, step):
        """"Open it now" from a tour screen.

        Muted, not the green Next — the same styling decision the guided birth
        made for 'Choose manually'. The primary action on a tour screen is to
        carry on reading; leaving for a mode is the escape, and dressing it like
        the main button would end the tour at its first screen.
        """
        b = Button(text=step.get("opens_label") or tr("Open it now"),
                   size_hint_y=None, height=dp(48),
                   bold=True, font_size="15sp", background_normal="",
                   background_color=theme.hex_to_rgba("#78866b"),
                   color=theme.hex_to_rgba("#f0f0f0"))
        b.bind(on_release=lambda *_: self._leave_for(step["opens"]))
        return b

    def _leave_for(self, screen):
        """Open a mode from the tour, recording that the walkthrough was seen.

        The marker is written on the way out. An operator who taps into BIRTH
        from the tour has finished with the walkthrough as far as they are
        concerned, and a medic that greeted them with it again on the next boot
        would be arguing with them.
        """
        self._finish(hand_back=False)
        if self._on_navigate:
            self._on_navigate(screen)

    # -- the recovery key --------------------------------------------------

    def _render_recovery_key(self, step):
        self._wizard(step, anim=self._notice_stage(),
                     on_next=self._open_key_ceremony)

    def _notice_stage(self):
        stage = BoxLayout(orientation="vertical", spacing=dp(8))
        note = self._notice_widget()
        if note is not None:
            stage.add_widget(note)
        stage.add_widget(Widget())
        return stage

    def _open_key_ceremony(self):
        """Hand to the existing write-it-down ceremony, then come back.

        Reused rather than rebuilt: ``RecoveryKeyScreen`` already asks three
        times, each more pointedly than the last, and that friction was
        specified deliberately (operator, 2026-08-02) in proportion to a
        consequence that cannot be undone.
        """
        from ui.screens.recovery_key_screen import RecoveryKeyScreen
        self._stop_current()
        self.clear_widgets()
        wrap = BoxLayout(orientation="vertical")
        wrap.add_widget(RecoveryKeyScreen(on_done=self._key_written))
        # A WAY OUT, even here. The ceremony has none of its own, and a screen
        # with no exit was reported as a trap twice in the guided birth and had
        # to be recovered by restarting the UI over SSH — which a field operator
        # does not have.
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(46), padding=[dp(20), 0, dp(20), dp(10)])
        back = Button(text=tr("←  Back"), size_hint=(None, 1), width=dp(150),
                      font_size="16sp", bold=True, background_normal="",
                      background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                      color=theme.hex_to_rgba(theme.COLORS["accent"]))
        back.bind(on_release=lambda *_: self._render())
        row.add_widget(back)
        row.add_widget(BoxLayout())
        wrap.add_widget(row)
        self.add_widget(wrap)

    def _key_written(self, key):
        self._recovery = key
        self._state = sf.advance(self._state, recovery_key_shown=True)
        self._goto(self._i + 1)

    def _render_type_it_back(self, step):
        stage = BoxLayout(orientation="vertical", spacing=dp(10))
        note = self._notice_widget()
        if note is not None:
            stage.add_widget(note)
        # EIGHT BOXES, LAID OUT THE WAY THE KEY WAS SHOWN — two rows of four
        # (operator, 2026-08-18). One long box asked the operator to reproduce
        # the grouping themselves: where the spaces go, whether hyphens count,
        # and whether what scrolled off the left was right. None of that is the
        # thing being checked. A box per group makes the shape of the answer the
        # same shape as the paper they are reading from, and each box holds
        # exactly one group, so four characters is visibly "this box is done".
        from kivy.uix.gridlayout import GridLayout
        grid = GridLayout(cols=recovery_key.GROUPS // 2, rows=2,
                          spacing=dp(8), size_hint_y=None, height=dp(124))
        self._key_boxes = []
        for i in range(recovery_key.GROUPS):
            box = TextInput(
                multiline=False, size_hint_y=None, height=dp(58),
                # Big enough that four characters read cleanly at arm's length
                # on the panel; the group can never scroll, so what is on screen
                # is always the whole of what was typed there.
                font_size="30sp", halign="center", write_tab=False)
            self._bind_keyboard(box)
            box.bind(text=lambda inst, val, n=i: self._key_box_typed(n, val))
            box.bind(on_text_validate=lambda inst, n=i: self._focus_key_box(n + 1))
            self._key_boxes.append(box)
            grid.add_widget(box)
        self._key_status = _grow("", "14sp", color="amber")
        stage.add_widget(grid)
        stage.add_widget(self._key_status)
        stage.add_widget(Widget())
        self._wizard(step, anim=stage)
        Clock.schedule_once(lambda _dt: self._focus_key_box(0), 0.3)

    def _typed_key(self) -> str:
        """The eight boxes read back as one key."""
        return "-".join((b.text or "").strip()
                        for b in getattr(self, "_key_boxes", []))

    def _focus_key_box(self, index):
        boxes = getattr(self, "_key_boxes", [])
        if 0 <= index < len(boxes):
            boxes[index].focus = True

    def _key_box_typed(self, index, value):
        """Keep a box to its own group, and move on when it is full.

        A group is four characters and the operator is copying from paper, so
        the moment the fourth lands the next box is where they are going. Any
        overflow — a fast typist, or a paste — is carried FORWARD rather than
        dropped, because silently eating a character the operator watched
        themselves type is the one behaviour this screen must not have.
        """
        boxes = getattr(self, "_key_boxes", [])
        if not boxes:
            return
        n = recovery_key.GROUP_LEN
        # Separators belong to the layout now, not the typing.
        cleaned = "".join(c for c in (value or "") if c not in "- _\t")
        if cleaned != value:
            boxes[index].text = cleaned
            return                                    # re-enters with the clean text
        if len(cleaned) > n:
            boxes[index].text = cleaned[:n]
            spill = cleaned[n:]
            if index + 1 < len(boxes):
                boxes[index + 1].text = (spill + boxes[index + 1].text)[:n * 2]
                self._focus_key_box(index + 1)
            return
        if len(cleaned) == n and index + 1 < len(boxes):
            self._focus_key_box(index + 1)
        self._check_typed_key()

    def _mark_wrong_groups(self, clear=False):
        """Tint the boxes whose group differs; return their 1-based numbers.

        Compared group-by-group on NORMALISED text, so the same handwriting
        folds that forgive the whole key forgive it here — a group must not be
        called wrong for a letter shape the check itself accepts.
        """
        boxes = getattr(self, "_key_boxes", [])
        want = recovery_key.groups(self._recovery)
        plain = theme.hex_to_rgba(theme.COLORS["text_primary"])
        bad = theme.hex_to_rgba(theme.COLORS["amber"])
        wrong = []
        for i, box in enumerate(boxes):
            got = recovery_key.normalize(box.text or "")
            expect = want[i] if i < len(want) else ""
            ok = clear or (got == expect)
            box.foreground_color = plain if ok else bad
            if not ok:
                wrong.append(i + 1)
        return wrong

    def _check_typed_key(self):
        """Compare what was typed against what was shown — normalised.

        Normalised because the key is read off paper: a handwritten I is a 1 and
        an O is a 0, and ``recovery_key.normalize`` already folds them. Refusing
        a correctly-written key over a letter shape would teach the operator
        their paper copy is wrong, which is the single most expensive wrong
        lesson this screen could teach.
        """
        typed = self._typed_key()
        if not recovery_key.normalize(typed):
            self._key_status.text = ""
            return
        if not recovery_key.is_wellformed(typed):
            self._key_status.text = (
                "Keep going — 32 characters, in eight groups of four.")
            return
        wrong = self._mark_wrong_groups()
        if wrong:
            # NAME THE GROUPS, don't just refuse (operator, 2026-08-19, stuck on
            # this screen with a correct-looking key). "That is not the key"
            # against 32 characters in one box is a puzzle with no method: the
            # operator re-reads all eight groups, finds nothing, and concludes
            # the medic is broken. Two characters were wrong on a page of 32.
            #
            # This does not leak a secret. The key was on the screen before this
            # one and is on the paper in their hand; pressing Back shows a key
            # again. This screen has never been the thing standing between an
            # intruder and the vault — the passphrase and the vault itself are.
            # What it IS, is the last chance to catch a bad transcription before
            # the only copy of the key is a page with a wrong letter on it.
            if len(wrong) == recovery_key.GROUPS:
                # Nothing lines up: naming all eight groups is noise, and this
                # is not a transcription slip — it is a different key.
                self._key_status.text = (
                    "That is not the key that was shown. Check it against your "
                    "paper — this is exactly the slip this step exists to catch.")
            elif len(wrong) == 1:
                self._key_status.text = (
                    f"Close — group {wrong[0]} does not match what was shown.")
            else:
                where = ", ".join(str(n) for n in wrong)
                self._key_status.text = (
                    f"Close — groups {where} do not match what was shown.")
            return
        self._mark_wrong_groups(clear=True)
        self._key_status.text = "That matches."
        self._state = sf.advance(self._state, recovery_key_verified=True)
        Clock.schedule_once(lambda _dt: self._goto(self._i + 1), 0.6)

    # -- the passphrase ----------------------------------------------------

    def _render_passphrase(self, step):
        stage = BoxLayout(orientation="vertical", spacing=dp(10))
        note = self._notice_widget()
        if note is not None:
            stage.add_widget(note)
        self._pw1 = self._pw_field("Passphrase")
        self._pw2 = self._pw_field("Type it again")
        self._pw_status = _grow("", "14sp", color="amber")
        stage.add_widget(self._pw1)
        stage.add_widget(self._pw2)
        stage.add_widget(self._reveal_row())
        stage.add_widget(self._pw_status)
        stage.add_widget(Widget())
        self._wizard(step, anim=stage, on_next=self._set_passphrase)

    def _pw_field(self, hint):
        f = TextInput(hint_text=hint, multiline=False, password=True,
                      size_hint_y=None, height=dp(54), font_size="26sp")
        self._bind_keyboard(f)
        return f

    def _reveal_row(self):
        """Show what is being typed, opt-in.

        The unlock screen already carries this and for the same reason (operator
        spec, 2026-08-01): a masked field on a touchscreen keypad is easy to get
        wrong with no way to check. It matters MORE here — this is the typing
        that decides what the passphrase actually is, and a typo made twice in
        agreement is a passphrase nobody knows.
        """
        row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(44),
                        spacing=dp(8))
        btn = Button(text=tr("Show"), size_hint=(None, 1), width=dp(110),
                     bold=True, font_size="15sp", background_normal="",
                     background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                     color=theme.hex_to_rgba(theme.COLORS["accent"]))

        def _toggle(*_):
            hidden = self._pw1.password
            self._pw1.password = self._pw2.password = not hidden
            btn.text = tr("Show") if not hidden else tr("Hide")
        btn.bind(on_release=_toggle)
        row.add_widget(btn)
        row.add_widget(BoxLayout())
        return row

    def _set_passphrase(self):
        a = self._pw1.text or ""
        b = self._pw2.text or ""
        if not a:
            self._pw_status.text = "Enter a passphrase."
            return
        if a != b:
            # Do NOT clear the first field. Wiping both on a mismatch means
            # re-typing something that was probably right, on a touchscreen
            # keypad, which is where the typo came from.
            self._pw2.text = ""
            self._pw_status.text = ("The two do not match. The second one has "
                                    "been cleared — type it again.")
            return
        self._passphrase = a
        self._state = sf.advance(self._state, passphrase_set=True)
        self._goto(self._i + 1)

    # -- choosing a level --------------------------------------------------

    def _render_level(self, step):
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(12))
        col.bind(minimum_height=col.setter("height"))
        note = self._notice_widget()
        if note is not None:
            col.add_widget(note)
        for policy, ok, why in sf.offered_levels(self._state):
            col.add_widget(self._level_card(policy, ok, why))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        scroll.add_widget(col)
        self._wizard(step, anim=scroll)

    def _level_card(self, policy, ok, why):
        """One option, with ``describe()``'s own words under it.

        The description is printed VERBATIM, warnings included — the model
        writes honest sentences precisely so a chooser does not have to
        editorialise, and the warning it attaches to every level ("a forgotten
        passphrase is not recoverable from the device") is the one an operator
        most needs to read at the moment they are choosing. Trimming it to make
        an option look better is how a security chooser becomes an advert.
        """
        d = describe(policy)
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        padding=dp(12), spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))
        from kivy.graphics import Color, Line, RoundedRectangle
        with box.canvas.before:
            Color(*theme.hex_to_rgba(theme.COLORS["surface"]))
            rect = RoundedRectangle(radius=[dp(8)] * 4)
            Color(*theme.hex_to_rgba(
                theme.COLORS["accent" if ok else "text_secondary"], 0.7))
            border = Line(width=dp(1.4))

        def _sync(*_):
            rect.pos, rect.size = box.pos, box.size
            border.rounded_rectangle = (box.x, box.y, box.width, box.height, dp(8))
        box.bind(pos=_sync, size=_sync)

        box.add_widget(_grow(level_name(policy), "19sp",
                             color="text_primary" if ok else "text_secondary",
                             bold=True))
        box.add_widget(_grow(d["asks"], "14sp"))
        box.add_widget(_grow(d["strength"], "14sp"))
        box.add_widget(_grow(d["warnings"], "13sp", color="warning_yellow"))
        box.add_widget(_grow(d["field"], "13sp"))
        # AND WHERE THE WAY BACK IN IS. Left off, the warnings above are the
        # last thing read before the choice, and an operator weighing "a
        # pattern leaves a smudge" against "a forgotten passphrase is not
        # recoverable from the device" picks out of fear of being locked out —
        # the exact outcome this ladder exists to avoid.
        #
        # ON EVERY CARD. This was once gated on PATTERN, back when the fallback
        # sentence ended "forget the pattern and you are inconvenienced, not
        # locked out" and there was no pattern to forget on the passphrase-only
        # card. The model no longer has a convenience door, so the sentence is
        # now about the recovery key and reads the same on all three — and the
        # card the gate used to skip is the one where "there is no back door"
        # is most load-bearing.
        box.add_widget(_grow(d["fallback"], "13sp"))
        if ok:
            btn = Button(text=tr("Choose this"), size_hint_y=None, height=dp(48),
                         bold=True, font_size="16sp", background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                         color=theme.hex_to_rgba(theme.COLORS["background"]))
            btn.bind(on_release=lambda *_, p=policy: self._choose_level(p))
            box.add_widget(btn)
        else:
            box.add_widget(_grow(why, "13.5sp", color="amber", bold=True))
        return box

    def _choose_level(self, policy):
        """Take the level and move on.

        The step list is recomputed from the new state, so the pattern and USB
        steps appear or vanish here — and this is the only place the counter is
        allowed to change length, because it is the only place the operator did
        something that changed it.
        """
        self._state = sf.advance(self._state, level=policy)
        self._goto(self._i + 1)

    # -- the pattern -------------------------------------------------------

    def _render_pattern(self, step):
        stage = BoxLayout(orientation="vertical", spacing=dp(8))
        note = self._notice_widget()
        if note is not None:
            stage.add_widget(note)
        first = self._pattern_first is None
        self._pattern_status = _grow(
            "Draw your pattern." if first else "Now draw it again to confirm.",
            "15sp", color="accent", bold=True)
        stage.add_widget(self._pattern_status)
        pad = PatternPad(on_complete=self._pattern_drawn)
        self._pad = pad
        stage.add_widget(pad)
        stage.add_widget(Widget())
        self._wizard(step, anim=stage)

    def _pattern_drawn(self, path):
        """One drawing finished.

        The rules are NOT applied here. ``encode_pattern`` owns the minimum
        length and the no-repeats rule and ``confirm_pattern`` owns the match —
        a widget that checked ``len(path) >= 4`` itself would be a second copy
        of a security rule, and the copy is the one that stops being updated.
        """
        if self._pattern_first is None:
            try:
                encode_pattern(path)
            except FactorError as e:
                self._pattern_status.text = str(e)
                self._pattern_status.color = theme.hex_to_rgba(
                    theme.COLORS["amber"])
                self._pad.clear()
                return
            self._pattern_first = list(path)
            self._pattern_status.text = "Now draw it again to confirm."
            self._pad.clear()
            return
        try:
            self._pattern = confirm_pattern(self._pattern_first, path)
        except FactorError as e:
            # BOTH drawings go, not just the second. Keeping the first would
            # mean the operator confirms against a pattern they may have got
            # wrong the first time and have no way to see.
            self._pattern_first = None
            self._pad.clear()
            self._pattern_status.text = str(e) + " Start again."
            self._pattern_status.color = theme.hex_to_rgba(theme.COLORS["amber"])
            return
        self._state = sf.advance(self._state, pattern_set=True)
        self._pattern_status.text = "The two match."
        Clock.schedule_once(lambda _dt: self._goto(self._i + 1), 0.5)

    # -- the USB key -------------------------------------------------------

    def _render_keyfile(self, step):
        stage = BoxLayout(orientation="vertical", spacing=dp(8))
        note = self._notice_widget()
        if note is not None:
            stage.add_widget(note)
        self._stick_status = _grow(usb_key.describe_targets([]), "15sp")
        stage.add_widget(self._stick_status)
        stage.add_widget(Widget())
        w = self._wizard(step, anim=stage, on_next=self._write_stick)
        # Nothing to write to yet. The button comes back the moment a stick is
        # seen — it is disabled rather than hidden so the operator can see what
        # is going to happen before they have plugged anything in.
        w.set_next_enabled(False)
        self._start_stick_poll()

    def _start_stick_poll(self):
        self._stop_stick_poll()

        def tick(_dt):
            def work():
                try:
                    found = usb_key.mount_points(lister=self._mount_lister)
                except Exception:
                    found = []
                Clock.schedule_once(lambda _d, f=found: self._sticks_seen(f), 0)
            threading.Thread(target=work, daemon=True).start()

        self._stick_poll = Clock.schedule_interval(tick, STICK_POLL_S)
        tick(0)

    def _stop_stick_poll(self):
        poll = getattr(self, "_stick_poll", None)
        if poll is not None:
            try:
                poll.cancel()
            except Exception:
                pass
            self._stick_poll = None

    def _sticks_seen(self, found):
        """Report what is mounted. Never writes — see the step's own comment."""
        if getattr(self, "_stick_status", None) is None:
            return
        self._stick_dirs = list(found)
        self._stick_status.text = usb_key.describe_targets(found)
        cur = getattr(self, "_current", None)
        if cur is not None and hasattr(cur, "set_next_enabled"):
            cur.set_next_enabled(len(found) == 1)

    def _write_stick(self):
        """Write the key file, on a deliberate press, and report what happened.

        Runs off the UI thread: a stick that has gone to sleep can take seconds
        to answer, and a frozen touchscreen during a write is how an operator
        decides to pull the device out.
        """
        dirs = list(self._stick_dirs)
        if len(dirs) != 1:
            self._stick_status.text = usb_key.describe_targets(dirs)
            return
        target = dirs[0]
        self._stick_status.text = f"Writing to {target}…"
        self._stop_stick_poll()

        def work():
            try:
                path, data = usb_key.write_key(target)
                secret = keyfile_secret(data)
                msg = f"Key written and read back: {path}"
                ok = True
            except Exception as e:      # noqa: BLE001 — one sentence, one catch
                ok, secret, msg = False, "", str(e)
            Clock.schedule_once(
                lambda _d: self._stick_written(ok, secret, msg), 0)
        threading.Thread(target=work, daemon=True).start()

    def _stick_written(self, ok, secret, msg):
        if not ok:
            self._stick_status.text = msg
            self._start_stick_poll()        # let them try another stick
            return
        self._keyfile_secret = secret
        self._state = sf.advance(self._state, keyfile_set=True)
        self._stick_status.text = msg
        Clock.schedule_once(lambda _dt: self._goto(self._i + 1), 0.8)

    # -- the summary -------------------------------------------------------

    def _render_summary(self, step):
        """What is set and what is not — read off the state and the disk.

        The enrolment runs HERE, at the end, not step by step. A ceremony
        abandoned in the middle must leave the medic exactly as it was: applying
        a passphrase at step five and a policy at step six would leave a medic
        that half-locks, whose operator does not know which half.
        """
        self._apply()
        col = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8))
        col.bind(minimum_height=col.setter("height"))
        try:
            exists = bool(self._vault_exists_fn())
        except Exception:
            # COULD NOT CHECK is its own answer, and it is not "yes".
            exists = False
        for ok, line in sf.summary_lines(self._state, vault_exists=exists):
            col.add_widget(_grow(("[OK]  " if ok else "—  ") + line, "15sp",
                                 color="green" if ok else "warning_yellow"))
        if self._applied_msg:
            col.add_widget(_grow(self._applied_msg, "14sp", color="accent"))
        scroll = ScrollView(do_scroll_x=False, bar_width=dp(4))
        scroll.add_widget(col)
        self._wizard(step, anim=scroll)

    def _apply(self):
        """Record the policy, and hand the factors to ``enrol_fn`` if there is one.

        Idempotent: the summary can be reached twice by stepping Back and
        forward, and re-running an enrolment would burn a second LUKS keyslot
        for the same passphrase.
        """
        if getattr(self, "_applied", False):
            return
        self._applied = True
        self._applied_msg = ""
        if self._state.level is None or not self._state.factors_ready:
            self._applied_msg = ("Nothing was applied — the security setup was "
                                 "not finished.")
            self._forget_secrets()
            return
        parts = {}
        if PATTERN in self._state.level.ordered:
            parts[PATTERN] = self._pattern
        if PASSPHRASE in self._state.level.ordered:
            parts[PASSPHRASE] = self._passphrase
        if KEYFILE in self._state.level.ordered:
            parts[KEYFILE] = self._keyfile_secret
        try:
            saved = save_policy(self._state.level)
        except Exception:
            saved = False
        if self._enrol_fn is None:
            self._applied_msg = (
                "Your choice is recorded." if saved else
                "Your choice could NOT be written to this card.")
            self._forget_secrets()
            return
        try:
            ok, msg = self._enrol_fn(parts, self._state.level, self._recovery)
        except Exception as e:      # noqa: BLE001
            ok, msg = False, str(e)
        self._applied_msg = msg or ("Applied." if ok else "Could not apply it.")
        # THE SECOND THE FACTORS HAVE BEEN HANDED OVER, DROP THEM. The tour runs
        # for another eight screens after this one and the object stays alive
        # for the whole session, because Settings can re-open it. A passphrase
        # sitting in an attribute for that long is a passphrase in a core dump,
        # and there is nothing left in this flow that needs it.
        self._forget_secrets()

    # -- shared bits -------------------------------------------------------

    @staticmethod
    def _bind_keyboard(field):
        """Attach the on-screen keypad. The panel has no physical keys, and a
        field that does not raise it is a field that cannot be filled in."""
        try:
            from ui.onscreen_keyboard import bind_field
            bind_field(field)
        except Exception:
            pass
