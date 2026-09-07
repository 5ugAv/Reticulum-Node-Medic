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
this layout has NO ScrollView, so text that does not fit is text nobody reads.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
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
            w.bind(pos=lambda wi, *_: setattr(wi._e, "pos",
                   (wi.center_x - dp(5), wi.center_y - dp(5))),
                   size=lambda wi, *_: setattr(wi._e, "size", (dp(10), dp(10))))
            self.add_widget(w)


class WizardStep(BoxLayout):
    """One guided step. ``on_next`` / ``on_back`` drive navigation; ``anim`` is an
    optional widget shown in the central stage."""

    def __init__(self, index, total, title, body, anim=None, on_next=None,
                 on_back=None, next_text=None, back_text=None,
                 hint="", warning="", input_widget=None, **kwargs):
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

        # central animation stage (flexes to fill the middle of the screen)
        self.stage = anim if anim is not None else Widget()
        self.add_widget(self.stage)

        body_lbl = Label(text=body, font_size=theme.font_sp("19sp"), halign="left",
                         valign="top",
                         size_hint_y=None, line_height=1.25,
                         color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        body_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                      texture_size=lambda i, ts: setattr(i, "height", ts[1]))
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

        nav = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62),
                        spacing=dp(12))
        self.back_btn = Button(text=back_text, font_size=theme.font_sp("18sp"),
                               bold=True,
                               size_hint_x=0.4, background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                               color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self.back_btn.bind(on_release=lambda *_: self._on_back and self._on_back())
        self.next_btn = Button(text=next_text, font_size=theme.font_sp("20sp"),
                               bold=True,
                               background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                               color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.next_btn.bind(on_release=lambda *_: self._on_next and self._on_next())
        nav.add_widget(self.back_btn)
        nav.add_widget(self.next_btn)
        self.add_widget(nav)
        self._nav = nav

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

    def hide_next(self):
        """Drop the Next button entirely — for steps the medic advances itself.

        On a "plug the thing in" step, detection drives the flow: the animation
        fires its Connected! burst and the wizard moves on by itself. The button
        was disabled until that moment and redundant after it, so it never did
        anything except invite a press that changed nothing (operator,
        2026-08-02). Back stays: leaving a step is still the operator's call.
        """
        if self.next_btn.parent is not None:
            self._nav.remove_widget(self.next_btn)
        self.back_btn.size_hint_x = 1        # Back takes the row on its own
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
