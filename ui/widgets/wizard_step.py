"""WizardStep — one instruction per screen for the guided birth flow.

A new operator shouldn't face a wall of fields. Each WizardStep shows ONE thing
to do: a step counter + progress dots, a big title, a roomy animation area (a
widget the caller supplies — see ui.widgets.birth_anims), large readable body
text, and Back / Next. The look here is deliberately plain and legible; Sophie
polishes the aesthetics once the flow and copy are right.
"""

from __future__ import annotations

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.widget import Widget

from ui import theme


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
                e = Ellipse()
            w._e = e
            w.bind(pos=lambda wi, *_: setattr(wi._e, "pos",
                   (wi.center_x - dp(5), wi.center_y - dp(5))),
                   size=lambda wi, *_: setattr(wi._e, "size", (dp(10), dp(10))))
            self.add_widget(w)


class WizardStep(BoxLayout):
    """One guided step. ``on_next`` / ``on_back`` drive navigation; ``anim`` is an
    optional widget shown in the central stage."""

    def __init__(self, index, total, title, body, anim=None, on_next=None,
                 on_back=None, next_text="Next  →", back_text="←  Back",
                 hint="", **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self.padding = dp(20)
        self.spacing = dp(14)
        self._on_next = on_next
        self._on_back = on_back

        top = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(46),
                        spacing=dp(8))
        counter = Label(text=f"Step {index + 1} of {total}", bold=True,
                        font_size="15sp", halign="left", valign="middle",
                        color=theme.hex_to_rgba(theme.COLORS["accent"]),
                        size_hint_y=None, height=dp(20))
        counter.bind(size=lambda i, v: setattr(i, "text_size", v))
        top.add_widget(counter)
        top.add_widget(_Dots(total, index))
        self.add_widget(top)

        title_lbl = Label(text=title, bold=True, font_size="27sp",
                          halign="left", valign="top", size_hint_y=None,
                          color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        title_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                       texture_size=lambda i, ts: setattr(i, "height", ts[1]))
        self.add_widget(title_lbl)

        # central animation stage (flexes to fill the middle of the screen)
        self.stage = anim if anim is not None else Widget()
        self.add_widget(self.stage)

        body_lbl = Label(text=body, font_size="19sp", halign="left", valign="top",
                         size_hint_y=None, line_height=1.25,
                         color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        body_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)),
                      texture_size=lambda i, ts: setattr(i, "height", ts[1]))
        self.add_widget(body_lbl)

        if hint:
            hint_lbl = Label(text=hint, font_size="14sp", halign="left", valign="top",
                             size_hint_y=None, height=dp(40),
                             color=theme.hex_to_rgba(theme.COLORS["warning_yellow"], 0.95))
            hint_lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
            self.add_widget(hint_lbl)

        nav = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(62),
                        spacing=dp(12))
        self.back_btn = Button(text=back_text, font_size="18sp", bold=True,
                               size_hint_x=0.4, background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                               color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        self.back_btn.bind(on_release=lambda *_: self._on_back and self._on_back())
        self.next_btn = Button(text=next_text, font_size="20sp", bold=True,
                               background_normal="",
                               background_color=theme.hex_to_rgba(theme.COLORS["green"]),
                               color=theme.hex_to_rgba(theme.COLORS["background"]))
        self.next_btn.bind(on_release=lambda *_: self._on_next and self._on_next())
        nav.add_widget(self.back_btn)
        nav.add_widget(self.next_btn)
        self.add_widget(nav)

    def start(self):
        """Start the stage animation, if it has one."""
        if hasattr(self.stage, "start"):
            self.stage.start()

    def stop(self):
        if hasattr(self.stage, "stop"):
            self.stage.stop()
