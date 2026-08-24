"""A small Samsung-style battery gauge for the home screen (Medic 2.0).

A horizontal battery pictogram: the body fills GREEN in proportion to the
pack's charge, and a small lightning bolt pulses inside it while the UPS is
charging. Reads :func:`monitor.ups.read_ups` on a slow clock, off the UI
thread.

HONESTY RULE: the icon exists only while a UPS actually answers on the bus.
Medic 1 has no HAT, so it renders nothing there — a battery gauge for a
battery that isn't fitted would be exactly the class of decoration this tool
exists to refuse. No reading -> no icon, never a guessed one.
"""

from __future__ import annotations

from typing import Optional

from kivy.animation import Animation
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, Triangle
from kivy.metrics import dp
from kivy.uix.widget import Widget

#: Seconds between UPS reads. Battery state moves slowly; the bus is shared.
POLL_S = 20.0

#: The bolt pictogram in unit space (x, y in 0..1), two triangles.
BOLT_TRIS = (
    (0.58, 0.95, 0.22, 0.45, 0.50, 0.45),
    (0.42, 0.55, 0.78, 0.55, 0.44, 0.05),
)


def battery_view(reading) -> Optional[dict]:
    """The pure view-model: what the icon should show for a UPS *reading*.

    ``None`` when there is nothing honest to draw (no UPS / no percentage).
    Otherwise ``{"fraction": 0.0..1.0, "charging": bool}``.
    """
    if reading is None or not getattr(reading, "present", False):
        return None
    pct = getattr(reading, "percent", None)
    if pct is None:
        return None
    try:
        frac = max(0.0, min(1.0, float(pct) / 100.0))
    except (TypeError, ValueError):
        return None
    return {"fraction": frac, "charging": bool(getattr(reading, "charging", False))}


class BatteryIcon(Widget):
    """Canvas-drawn battery: outline + nub, proportional green fill, pulsing
    bolt while charging. Size is fixed and small (status-bar scale)."""

    def __init__(self, **kw):
        kw.setdefault("size_hint", (None, None))
        kw.setdefault("size", (dp(34), dp(16)))
        super().__init__(**kw)
        self._view = None          # last battery_view result
        self._bolt_alpha = None    # kivy Color for the bolt (animated)
        self.opacity = 0           # hidden until a UPS answers
        self.bind(pos=self._redraw, size=self._redraw)
        self._ev = Clock.schedule_interval(self._poll, POLL_S)
        Clock.schedule_once(self._poll, 0)

    # -- data ---------------------------------------------------------------
    def _poll(self, _dt):
        import threading

        def work():
            view = None
            try:
                from monitor.ups import read_ups
                view = battery_view(read_ups())
            except Exception:
                view = None                     # no UPS is a normal state
            Clock.schedule_once(lambda _d: self._apply(view), 0)

        threading.Thread(target=work, daemon=True).start()

    def _apply(self, view):
        self._view = view
        self.opacity = 1 if view is not None else 0
        self._redraw()

    # -- drawing ------------------------------------------------------------
    def _redraw(self, *_a):
        self.canvas.clear()
        Animation.cancel_all(self)
        v = self._view
        if v is None:
            return
        x, y, w, h = self.x, self.y, self.width, self.height
        nub_w = dp(3)
        body_w = w - nub_w
        inset = dp(2)
        with self.canvas:
            # body outline + the nub on the right
            Color(0.85, 0.85, 0.85, 0.9)
            Line(rounded_rectangle=(x, y, body_w, h, dp(3)), width=1.1)
            Rectangle(pos=(x + body_w + dp(0.5), y + h * 0.3),
                      size=(nub_w - dp(1), h * 0.4))
            # proportional green fill
            Color(0.18, 0.80, 0.35, 1)
            fill_w = max(0.0, (body_w - 2 * inset) * v["fraction"])
            Rectangle(pos=(x + inset, y + inset),
                      size=(fill_w, h - 2 * inset))
            # pulsing bolt while charging
            if v["charging"]:
                self._bolt_alpha = Color(1, 1, 1, 0.95)
                bx, bw = x + body_w * 0.22, body_w * 0.56
                for t in BOLT_TRIS:
                    pts = []
                    for i in range(0, 6, 2):
                        pts += [bx + t[i] * bw, y + t[i + 1] * h]
                    Triangle(points=pts)
        if v["charging"] and self._bolt_alpha is not None:
            pulse = (Animation(a=0.25, d=0.7) + Animation(a=0.95, d=0.7))
            pulse.repeat = True
            pulse.start(self._bolt_alpha)
