"""When to tell the operator there is more below — pure, no Kivy.

Split out from ``ui.widgets.scroll_hint`` so the RULE can be tested where Kivy
isn't importable: CI has no Kivy, and the test suite installs process-global
Kivy stubs that only cover the submodules already in use. Same split the rest of
the codebase uses (``ui.birth_guide_flow`` pure, ``birth_guide_screen`` drawn).
"""

from __future__ import annotations


def more_below(scroll_y: float, viewport_height: float,
               content_height: float, slack: float = 8.0) -> bool:
    """Is there content below the fold right now?

    ``scroll_y`` follows Kivy's convention: 1.0 is the top, 0.0 the bottom.
    *slack* absorbs the pixel or two of overflow almost every layout rounds to —
    a chevron that never goes away teaches the operator to ignore it, which is
    worse than not having one.
    """
    try:
        if content_height <= viewport_height + slack:
            return False
        hidden = (content_height - viewport_height) * float(scroll_y)
        return hidden > slack
    except (TypeError, ValueError):
        return False
