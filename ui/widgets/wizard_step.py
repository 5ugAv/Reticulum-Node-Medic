"""WizardStep — one instruction per screen for the guided birth flow.

A new operator shouldn't face a wall of fields. Each WizardStep shows ONE thing
to do: a step counter + progress dots, a big title, a roomy animation area (a
widget the caller supplies — see ui.widgets.birth_anims), large readable body
text, and Back / Next. The look here is deliberately plain and legible; a
design pass polishes the aesthetics once the flow and copy are right.

TYPE SIZES GO THROUGH ``theme.font_sp``. This screen carries the longest prose
in the app and was the one place that never joined the readability scale — it
still shipped its literal "14sp" hint while every other screen's 14sp had grown
to 17.5sp. So the birth walkthrough, read standing at a bench, was the SMALLEST
text on the medic (operator, 2026-08-11: "the text is so small and there's so
much text").

Bigger type only fits because the copy was cut in the same edit — see
``ui.birth_guide_flow``. The stacked height of every step is asserted against
the panel by ``tests/test_birth_guide.py::test_no_guided_step_overflows_the_panel``:
the guided birth's layout has NO ScrollView, so text that does not fit is text
nobody reads.

THE BUTTON BAR IS A FIXED BAR ON THE BOTTOM EDGE. On a scrolling step (the
first-use tour, ``scroll_body=True``) the body is the ONE part of the page
with a ``size_hint_y``; everything else — counter, title, picture shelf, hint,
warning and the Back / Next bar — is a fixed height, and the bar is the last
child, which in a vertical BoxLayout is the bottom edge. So the words take
whatever height is left and scroll inside it; they cannot grow the page. The
walkthrough photographed on a fresh clone (2026-10-07) had four pages whose
body ran past the bottom of the glass and two whose buttons went with it, and
``_keep_bar_on_glass`` is the second lock: if the fixed parts ever stack
taller than the page, the picture shelf gives way before the bar can.
``tests/test_setup_wizard_layout.py`` pins both.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget

from ui import theme
from ui.i18n import tr


class _Dots(BoxLayout):
    """A row of progress dots — the current step filled with the accent colour."""

    def __init__(self, total, current, **kwargs):
        super().__init__(orientation="horizontal", spacing=dp(8),
                         size_hint_y=None, height=dp(14), **kwargs)
        from kivy.graphics import Color, Ellipse
        self._specs = []
        for i in range(total):
            w = Widget(size_hint=(None, 1), width=dp(12))
            with w.canvas:
                on = i == current
                Color(*theme.hex_to_rgba(theme.COLORS["accent" if on else "surface"]))
                # Sized HERE, not only in the binding below: Ellipse() with
                # no size defaults to 100x100, and the size= binding fires
                # only if a later layout pass happens to change the widget's
                # size - so a dot could render as a huge circle sitting on
                # top of the "Step 4 of 10" text above it.
                e = Ellipse(size=(dp(10), dp(10)))
            w._e = e
            w.bind(pos=self._place, size=self._place)
            self.add_widget(w)

    @staticmethod
    def _place(wi, *_):
        # Re-centred on BOTH events, from the raw x/y/width/height - never
        # center_x/center_y. Inside the size dispatch those aliases still hold
        # the value cached for the default 100 px height, so the ellipse landed
        # a full row too high, over the counter, and nothing moved it again
        # unless the screen happened to slide in (glass, 2026-10-05).
        wi._e.size = (dp(10), dp(10))
        wi._e.pos = (wi.x + (wi.width - dp(10)) / 2.0,
                     wi.y + (wi.height - dp(10)) / 2.0)


class WizardStep(BoxLayout):
    """One guided step. ``on_next`` / ``on_back`` drive navigation; ``anim`` is an
    optional widget shown in the central stage."""

    #: Height of a picture stage on a scrolling step (dp): tall enough for a
    #: poster card to be read as the card it is, small enough to leave three
    #: lines of body under a one-line title on the 480 dp panel.
    PICTURE_STAGE_DP = 120

    #: Height of the Back / Next bar (dp). FIXED: the bar never takes a share
    #: of the page, so nothing above it can make it shorter or move it. It is
    #: the last child added, which in a vertical BoxLayout is the bottom edge.
    #: tests/test_birth_guide.py mirrors this as _NAV_H — keep them in step.
    NAV_H = 62

    def __init__(self, index, total, title, body, anim=None, on_next=None,
                 on_back=None, next_text=None, back_text=None,
                 hint="", warning="", input_widget=None, show_back=True,
                 scroll_body=False, stage_height=None, extra_nav=None,
                 **kwargs):
        """``scroll_body`` (the setup tour's shape, readiness ledger #146):
        the BODY becomes the flexible, scrolling middle and the animation
        stage — if there is one — takes a FIXED ``stage_height`` dp instead,
        so a long explanation can never push the picture to nothing or run
        off the panel. The guided birth keeps the default: a fixed body that
        the height tests hold under the panel, and a flexible stage.
        ``extra_nav`` is a widget for the nav row between Back and Next (the
        tour's "Open it now")."""
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.padding = dp(20)
        self.spacing = dp(14)
        self._on_next = on_next
        self._on_back = on_back
        # i18n: wrapped — default nav labels + "Step X of Y" counter
        if next_text is None:
            next_text = tr("Next  →")
        if back_text is None:
            back_text = tr("←  Back")

        # 46 left only 4px of slack over its children (20 + 8 spacing + 14);
        # any rounding pushed the dots into the counter's line.
        top = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(56),
                        spacing=dp(10))
        counter = Label(text=tr("Step {n} of {total}").format(n=index + 1, total=total),
                        bold=True,
                        font_size=theme.font_sp("15sp"), halign="left", valign="middle",
                        color=theme.hex_to_rgba(theme.COLORS["accent"]),
                        size_hint_y=None, height=dp(20))
        counter.bind(size=lambda i, v: setattr(i, "text_size", v))
        top.add_widget(counter)
        top.add_widget(_Dots(total, index))
        self.add_widget(top)

        title_lbl = Label(text=title, bold=True, font_size=theme.font_sp("27sp"),
                          halign="left", valign="top", size_hint_y=None,
                          color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        title_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                       texture_size=lambda i, ts: setattr(i, "height", ts[1]))
        self.add_widget(title_lbl)

        # optional input (e.g. the node-name field) sits right under the title
        if input_widget is not None:
            self.add_widget(input_widget)

        # central animation stage (flexes to fill the middle of the screen —
        # or, on a scrolling step, a fixed-height picture shelf, or nothing)
        self.stage = anim if anim is not None else Widget()
        #: The picture shelf's full height on a scrolling step (px); what
        #: _keep_bar_on_glass gives back once there is room again.
        self._shelf_px = 0.0
        if scroll_body:
            if anim is not None:
                self.stage.size_hint_y = None
                self.stage.height = dp(stage_height if stage_height is not None
                                       else self.PICTURE_STAGE_DP)
                self._shelf_px = float(self.stage.height)
                self.add_widget(self.stage)
        else:
            self.add_widget(self.stage)

        body_lbl = Label(text=body, font_size=theme.font_sp("19sp"), halign="left",
                         valign="top",
                         size_hint_y=None, line_height=1.25,
                         color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        body_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                      texture_size=lambda i, ts: setattr(i, "height", ts[1]))
        self.body_lbl = body_lbl
        self.body_scroll = None
        if scroll_body:
            sv = ScrollView(do_scroll_x=False, bar_width=dp(4), size_hint_y=1)
            sv.add_widget(body_lbl)
            self.body_scroll = sv
            self.add_widget(sv)
        else:
            self.add_widget(body_lbl)

        if hint:
            # Height FOLLOWS the wrapped text, exactly like the body above.
            # Pinned at dp(40) it fitted two lines; the per-model connector
            # guidance runs to six ("On a Pi 3A+ it's the full-size USB-A
            # socket — its micro-USB is power only..."), and the overflow drew
            # straight through the body text. Two paragraphs on top of each
            # other, on the step that tells you which socket to use (photo,
            # 2026-08-09).
            hint_lbl = Label(text=hint, font_size=theme.font_sp("14sp"),
                             halign="left", valign="top",
                             size_hint_y=None, height=dp(40),
                             color=theme.hex_to_rgba(theme.COLORS["warning_yellow"], 0.95))
            hint_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                          texture_size=lambda i, ts: setattr(i, "height", ts[1]))
            self.add_widget(hint_lbl)

        # A warning is weightier than a hint (e.g. "no antenna can damage the board"):
        # bold dark text on a solid amber caution strip so it can't be skimmed past.
        if warning:
            from kivy.graphics import Color, RoundedRectangle
            warn_lbl = Label(text=warning, font_size=theme.font_sp("15sp"), bold=True,
                             halign="left",
                             valign="middle", size_hint_y=None, padding=(dp(12), dp(10)),
                             color=theme.hex_to_rgba(theme.COLORS["background"]))
            from kivy.graphics import Line
            with warn_lbl.canvas.before:
                Color(*theme.hex_to_rgba(theme.COLORS["warning_yellow"]))
                warn_lbl._bg = RoundedRectangle(radius=[dp(8)] * 4)
                # Red border, matching ui.widgets.callout: one look for "this one
                # you must act on", so the operator learns it once (operator,
                # 2026-08-02). Yellow alone reads as merely emphasised.
                Color(0.84, 0.0, 0.0, 1)
                warn_lbl._border = Line(rounded_rectangle=(0, 0, 0, 0, dp(8)),
                                        width=dp(1.6))

            def _fit(i, *_a):
                i._bg.pos, i._bg.size = i.pos, i.size
                i._border.rounded_rectangle = (i.x, i.y, i.width, i.height, dp(8))
            warn_lbl.bind(
                width=lambda i, w: setattr(i, "text_size", (w - dp(24), None)),
                texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(20)),
                pos=_fit, size=_fit)
            self.add_widget(warn_lbl)

        # THE BAR: a fixed height, never a share, always the last child — the
        # bottom edge of the page on every step (see the module docstring).
        nav = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(self.NAV_H), spacing=dp(12))
        # show_back=False: the screen lives under the bottom bar, whose '←'
        # already does on_back (operator, 2026-09-22: "that little back
        # button can be removed now"). The setup wizard has no bar and
        # keeps its own. back_btn stays an attribute (None) for callers.
        self.back_btn = None
        if show_back:
            self.back_btn = Button(text=back_text, font_size=theme.font_sp("18sp"),
                                   bold=True,
                                   size_hint_x=None, width=dp(120), background_normal="",
                                   background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                                   color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            # Sized to its caption, never under 120 dp: with the tour's third
            # button in the row a 0.4 share shrank to 116 px and "←  Kembali"
            # / "←  Tillbaka" ran past the button's edges (2026-10-05).
            self.back_btn.bind(texture_size=lambda b, ts: setattr(
                b, "width", max(dp(120), ts[0] + dp(28))))
            self.back_btn.bind(on_release=lambda *_: self._on_back and self._on_back())
        self.next_btn = Button(text=next_text, font_size=theme.font_sp("20sp"),
                               bold=True, halign="center", valign="middle",
                               background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                               color=theme.hex_to_rgba(theme.COLORS["background"]))
        # The caption stays INSIDE the green: with a third button in the row
        # "Passer pour le moment →" (363 px) ran past a 253 px Next on the
        # firstborn tour step (sandbox, 2026-10-05). A long caption wraps to a
        # second line rather than spilling over its neighbours.
        self.next_btn.bind(width=lambda i, w: setattr(i, "text_size", (w - dp(16), None)))
        self.next_btn.bind(on_release=lambda *_: self._on_next and self._on_next())
        if self.back_btn is not None:
            nav.add_widget(self.back_btn)
        if extra_nav is not None:
            nav.add_widget(extra_nav)
        nav.add_widget(self.next_btn)
        self.add_widget(nav)
        self._nav = nav
        if scroll_body:
            # The second lock on the bar (the first is the fixed heights
            # above): re-checked whenever the page is resized or any fixed
            # part changes height — a title that wraps, a hint that grows.
            self.bind(size=self._keep_bar_on_glass,
                      minimum_height=self._keep_bar_on_glass)
            self._keep_bar_on_glass()

    def _keep_bar_on_glass(self, *_):
        """Scrolling steps only: the fixed parts must never stack taller than
        the page, because the bar is the last of them.

        A vertical BoxLayout gives its one flexible child (here the body's
        ScrollView) whatever is left after the fixed children, and that is
        clamped at zero — so if the fixed children alone outgrow the page the
        stack spills over the edge. The body cannot cause that (it scrolls),
        but a three-line title over a six-line hint could. When it happens the
        PICTURE SHELF gives way, down to nothing: the words and the buttons
        are what the operator acts on, the card is a reminder of where to tap.
        It grows back the moment there is room again (a resize, a shorter
        hint). On a page that fits — every tour page on the 853 dp panel —
        this changes nothing.
        """
        stage = self.stage
        if stage.parent is not self or stage.size_hint_y is not None:
            return                      # no shelf on this step
        if self.height <= 0:
            return                      # not laid out yet
        # minimum_height counts every fixed child plus padding and spacing;
        # take the shelf's own share out to see what the REST needs.
        others = self.minimum_height - stage.height
        room = max(0.0, self.height - others)
        want = min(self._shelf_px, room)
        if abs(stage.height - want) > 0.5:
            stage.height = want

    def set_status(self, text: str):
        """A live one-line narration under the body: what the medic is seeing
        RIGHT NOW on a self-advancing step. A watcher that refuses silently
        reads as a hang (skyfinger, 2026-08-14 — the medic had FOUND the Pi
        and was rightly refusing an unprovable card, and the operator watched
        a still screen for minutes). Created on first use; the flexible
        animation stage absorbs its height, so nothing overflows."""
        if not getattr(self, "_status_lbl", None):
            lbl = Label(text="", font_size=theme.font_sp("14sp"),
                        halign="left", valign="top", size_hint_y=None,
                        color=theme.hex_to_rgba(theme.COLORS["accent"]))
            lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                     texture_size=lambda i, ts: setattr(i, "height", ts[1]))
            self._status_lbl = lbl
            # children[0] is the nav row (last added); index=1 sits just above
            self.add_widget(lbl, index=1)
        if self._status_lbl.text != text:
            self._status_lbl.text = text

    def hide_next(self, heartbeat: bool = True):
        """Drop the Next button entirely — for steps the medic advances itself.

        ``heartbeat=False`` for a step the OPERATOR advances by typing, choosing
        or drawing (the setup wizard's type-it-back, level and pattern steps):
        there the "keeping watch — you don't need to press anything" pulse was
        a false sentence over a keypad waiting to be used (readiness ledger
        #175). The pulse belongs only to hardware waits.

        On a "plug the thing in" step, detection drives the flow: the animation
        fires its Connected! burst and the wizard moves on by itself. The button
        was disabled until that moment and redundant after it, so it never did
        anything except invite a press that changed nothing (operator,
        2026-08-02). Back stays: leaving a step is still the operator's call.
        """
        if self.next_btn.parent is not None:
            self._nav.remove_widget(self.next_btn)
        # The guided birth builds every step with show_back=False (the bottom
        # bar's arrow is Back), so back_btn is None there — and this line
        # killed the whole app on the first self-advancing Pi step (readiness
        # sweep, 2026-10-03).
        if self.back_btn is not None:
            self.back_btn.size_hint_x = 1    # Back takes the row on its own
        if heartbeat:
            self._start_heartbeat()

    def _start_heartbeat(self):
        """A self-advancing step has no button, so a keeper waiting on the medic
        to sense hardware sees a still screen and thinks it hung, then pulls the
        board mid-flash (walkthrough 2026-08-26). A slow-pulsing 'keeping watch'
        line gives the screen a visible pulse — pure decoration, separate from
        set_status's narration so the two never fight. Self-cancels once the
        step is detached, so no Clock leaks after navigation."""
        from kivy.clock import Clock
        if getattr(self, "_heartbeat_ev", None):
            return
        # WRAPPED FOR TRANSLATION, 2026-09-07. This was a bare literal, so the
        # one line on a self-advancing screen that tells a keeper nothing is
        # broken rendered in English in all eleven carried languages — on the
        # steps where the screen sits still for a minute with no button. The
        # i18n coverage guard only sees strings already inside tr(), so it
        # could never have caught this.
        beat = Label(text=tr("●  keeping watch — you don't need to press anything"),
                     font_size=theme.font_sp("13sp"), halign="left",
                     valign="middle", size_hint_y=None, height=dp(22),
                     color=theme.hex_to_rgba(theme.COLORS["accent"]))
        beat.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        self._heartbeat_lbl = beat
        self.add_widget(beat, index=1)      # just above the nav row
        self._heartbeat_up = True

        def pulse(_dt):
            if self.parent is None:         # step navigated away -> stop
                self._heartbeat_ev.cancel()
                self._heartbeat_ev = None
                return False
            self._heartbeat_up = not self._heartbeat_up
            beat.opacity = 1.0 if self._heartbeat_up else 0.35
            return True
        self._heartbeat_ev = Clock.schedule_interval(pulse, 0.6)

    def show_next(self):
        """Put the Next button back — for a wait that has gone on long enough
        to need a way out.

        The counterpart to hide_next. A step the medic normally carries itself
        should NOT offer a button while it is still working: an early press
        skips past hardware that was merely slow, which is how a Pi still
        expanding its card gets abandoned. But a poll that never fires must not
        strand anyone either — that trap cost a whole step this morning. So the
        button is withheld while the wait is reasonable and appears once it
        clearly is not.
        """
        if self.next_btn.parent is None:
            if self.back_btn is not None:
                self.back_btn.size_hint_x = 0.4
            self._nav.add_widget(self.next_btn)

    def set_next_enabled(self, on: bool):
        """Gray out / re-enable the Next button — used to gate a step until its
        precondition is met (e.g. a board has been detected on USB)."""
        self.next_btn.disabled = not on
        self.next_btn.background_color = theme.hex_to_rgba(
            theme.COLORS["green" if on else "surface"])
        self.next_btn.color = theme.hex_to_rgba(
            theme.COLORS["background" if on else "text_secondary"])

    def start(self):
        """Start the stage animation, if it has one."""
        if hasattr(self.stage, "start"):
            self.stage.start()

    def stop(self):
        if hasattr(self.stage, "stop"):
            self.stage.stop()
