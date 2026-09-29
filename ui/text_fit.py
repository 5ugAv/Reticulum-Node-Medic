"""The box follows the text — one rule for every paragraph on the medic.

The 2026-09-29 screen walk (32 screens captured over the control socket)
found seven screens clipping or overprinting their own sentences: the
recovery-key warning, the trusted-operators intro and every clone's "descended
from" line, the notifications explainer, Home mode in Settings, the radio
defaults warning, the date-time sync status, and the whole MITOSIS parts list.
All the same defect: a Label pinned to the height its sentence needed on the
day it was measured, then the type scale lifted and the sentence wrapped onto
a line the box did not have.

Two shapes of the bug, one cure:

* ``h=NN`` — an explicit fixed height.  Fits the string it was measured
  against and clips the next one (and every translation).
* the "grow" idiom done wrong — ``bind(size → text_size)`` PLUS
  ``bind(texture_size → height)``.  With BOTH text_size dimensions set, Kivy
  renders the texture at exactly text_size, so ``texture_size == text_size``
  and the height freezes at whatever it was on the first render: the
  UN-wrapped natural height.  The wrapped text is then drawn ``valign=middle``
  into a box too short for it — clipped equally top and bottom.  That is the
  MITOSIS parts list losing its first and last items.

``grow_to_text`` fixes both without a screen having to change its Label
helper: text_size is held to ``(width, None)`` — and RE-held whenever another
observer sets a height into it — so the texture is the wrapped text's true
size, and the widget's height follows the texture.
"""
from __future__ import annotations

from kivy.metrics import dp


def grow_to_text(lbl, extra_dp: float = 0):
    """Make *lbl* exactly as tall as its wrapped text, and keep it so.

    Safe to apply to a label that already binds ``size → text_size``: the
    ``text_size`` observer below undoes any height another binding writes,
    deterministically and regardless of binding order.
    """
    lbl.size_hint_y = None

    def _width_only(i, ts):
        if ts is not None and ts[1] is not None:
            i.text_size = (ts[0], None)

    lbl.bind(text_size=_width_only)
    lbl.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    lbl.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(extra_dp)))
    lbl.text_size = (lbl.width, None)
    return lbl
