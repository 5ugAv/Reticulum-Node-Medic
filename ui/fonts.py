"""Global display font selection — one font per run, chosen by language.

The app's language applies on restart (screens are built once), so the font can
be chosen ONCE at startup from the saved language:

  * default → **DejaVuSans** (bundled with Kivy): Latin + Cyrillic, so every
    Latin language and Russian render.
  * Japanese → a Latin+CJK font (Noto Sans JP) if one is present, since DejaVu
    has no CJK and a CJK-only font can't draw the Latin proper nouns Japanese
    strings keep (Node Medic, RNode, VITALS…).

We register the chosen face under the name ``Roboto`` — Kivy's default font name
AND the name a few screens pass explicitly — so overriding it re-points BOTH the
implicit default and those explicit uses in one shot. ``RobotoMono`` (used only
for hex identity hashes, i.e. Latin) is left alone.

This module imports Kivy, so it is only touched at app start (never in the pure
i18n layer or in tests that avoid a display).
"""

from __future__ import annotations

import os

from ui.i18n import current_language, japanese_font_path


def _kivy_font_dir() -> str:
    import kivy
    return os.path.join(os.path.dirname(kivy.__file__), "data", "fonts")


def dejavu_path() -> str:
    """DejaVuSans bundled with Kivy (Latin + Cyrillic)."""
    return os.path.join(_kivy_font_dir(), "DejaVuSans.ttf")


def font_for_language(code: str) -> str:
    """The face to use as the global font for language *code*: the Japanese
    Latin+CJK font when Japanese is active and present, else DejaVuSans."""
    if code == "ja":
        ja = japanese_font_path()
        if ja:
            return ja
    return dejavu_path()


def configure_fonts() -> str:
    """Register the run's global display font (see module docstring). Call ONCE at
    startup, before any screen is built. Returns the font path registered.

    Registering under the name ``Roboto`` overrides Kivy's default face, so every
    Label — implicit or ``font_name="Roboto"`` — uses it. Idempotent and
    defensive: any failure leaves Kivy's built-in default in place rather than
    crashing the app over a font."""
    path = font_for_language(current_language())
    try:
        from kivy.core.text import LabelBase
        # Bold/italic variants: DejaVu ships them; a single-file Noto JP may not,
        # so fall back to the regular face for those slots (Kivy synthesises).
        d = _kivy_font_dir()
        bold = os.path.join(d, "DejaVuSans-Bold.ttf")
        variants = {"fn_regular": path}
        if path == dejavu_path() and os.path.exists(bold):
            variants["fn_bold"] = bold
        LabelBase.register(name="Roboto", **variants)
    except Exception:
        # Never let a font problem stop the medic from starting.
        pass
    return path
