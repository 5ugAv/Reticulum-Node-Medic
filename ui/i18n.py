"""Offline, source-keyed internationalisation (i18n) for Node Medic.

Node Medic runs in the field with no network, so translation is fully OFFLINE:
each language ships as a bundled JSON catalog under ``assets/i18n/<code>.json``.

Design — SOURCE-KEYED catalogs
------------------------------
A catalog maps the **English source string → its translation**::

    { "Settings": "Ajustes", "About": "Acerca de", ... }

Because the key IS the English text, wrapping a string for translation is purely
mechanical — you wrap the exact words already on screen::

    from ui.i18n import tr
    label.text = tr("Settings")

and if a key is missing from a catalog (or the catalog is absent / corrupt) the
English source falls straight through. Missing translations therefore DEGRADE to
English — they never crash and never show a blank. There is no separate ``en``
catalog to keep in sync; English is the source of truth.

How to wrap MORE strings
-------------------------
1. ``from ui.i18n import tr`` (alias ``_``) in the screen/widget module.
2. Replace a user-facing literal ``"Foo"`` with ``tr("Foo")``. Leave logic,
   keys, log lines and proper nouns alone (Node Medic, Reticulum, Columba,
   Sideband, RNode, LoRa, Kin — see ``DO_NOT_TRANSLATE``).
3. Mark the module (or the wrapped block) with a ``# i18n: wrapped`` comment so
   the next dev can see how far coverage reaches.
4. Add the new English string as a key to every ``assets/i18n/<code>.json``.

How to add a LANGUAGE
---------------------
1. Create ``assets/i18n/<code>.json`` translating each wrapped English string.
2. Register the code in ``_LANGUAGES`` below with its native + English name.
3. Keep it LATIN-SCRIPT for now (see the font caveat). A language only shows in
   the picker once it has a catalog AND is renderable by the bundled font.

FONT / SCRIPT CAVEAT (be honest)
--------------------------------
Kivy's default Roboto font covers Latin only. At startup ``ui.fonts`` switches
the app's global font to **DejaVuSans** (bundled with Kivy), which covers Latin
+ Cyrillic — so Spanish/French/German/Portuguese/Italian/Indonesian/Swedish/
Polish AND Russian all render. ``available_languages()`` gates on
``_is_renderable``: those are always offered.

**Japanese** (CJK) needs its own font — DejaVu has no CJK, and a CJK-only
fallback can't draw the Latin proper nouns Japanese strings keep. So Japanese is
offered ONLY when a Latin+CJK font (Noto Sans JP) is present (``japanese_font_
path``); ``ui.fonts`` loads that font when Japanese is the active language.
Right-to-left (Arabic/Hebrew) remains FUTURE SCOPE (needs RTL layout work).

Persistence follows the project's plain-file pref pattern (see
``workflows.node_mode.load_home_profile``): a single file under
``~/.reticulum-node-medic/language``, OSError-guarded, default ``"en"``.

This module is PURE (no Kivy import) so it stays unit-testable and CI-safe.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

#: Where the chosen language code is persisted (plain file, like node_mode prefs).
LANGUAGE_FILE = os.path.expanduser("~/.reticulum-node-medic/language")

#: Bundled catalogs live here: assets/i18n/<code>.json (repo-root/assets/i18n).
_I18N_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "i18n")

DEFAULT_LANGUAGE = "en"

#: Known languages: code -> (native_name, english_name). English is the source.
#: Add a row here (Latin-script only for now) when you add a catalog. Order here
#: is the order the picker shows them in.
_LANGUAGES: List[Tuple[str, str, str]] = [
    ("en", "English", "English"),
    ("es", "Español", "Spanish"),
    ("fr", "Français", "French"),
    ("de", "Deutsch", "German"),
    ("pt", "Português", "Portuguese"),
    ("it", "Italiano", "Italian"),
    ("id", "Bahasa Indonesia", "Indonesian"),
    ("sv", "Svenska", "Swedish"),
    ("pl", "Polski", "Polish"),
    ("ru", "Русский", "Russian"),
    ("ja", "日本語", "Japanese"),
]

#: Codes the DEFAULT display font (DejaVuSans — see ui.fonts) can render: the
#: Latin scripts plus Cyrillic. The app switches its global font to DejaVuSans at
#: startup, so all of these are safe to offer. Japanese is NOT here — CJK needs
#: its own font and is gated separately on that font actually being present.
_RENDERABLE = {"en", "es", "fr", "de", "pt", "it", "id", "sv", "pl", "ru",
               "nl", "ca", "gl"}

#: Where a carried Japanese font would live (mirrors the other carried assets).
_ASSETS_FONTS = os.path.join(os.path.dirname(_I18N_DIR), "fonts")

#: Fonts that render Japanese *and* the Latin proper nouns that Japanese UI
#: strings keep (Node Medic, RNode…). A pure CJK-only fallback (e.g. Droid Sans
#: Fallback) is deliberately NOT listed — it can't draw the Latin parts. Japanese
#: only appears in the picker when one of these exists, else it would paint tofu.
_JA_FONT_CANDIDATES = (
    os.path.join(_ASSETS_FONTS, "NotoSansJP-Regular.ttf"),
    os.path.join(_ASSETS_FONTS, "NotoSansJP-Regular.otf"),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf",
)


def japanese_font_path() -> Optional[str]:
    """Absolute path to a bundled/installed font that can render Japanese + Latin,
    or None. Used both to gate Japanese in the picker and (by ui.fonts) to load
    it. Pure ``os.path`` — no Kivy — so it stays unit-testable."""
    for path in _JA_FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _is_renderable(code: str) -> bool:
    """Whether the app can actually display *code* without tofu boxes: the
    DejaVu-covered scripts always, Japanese only when a CJK+Latin font is present."""
    if code in _RENDERABLE:
        return True
    if code == "ja":
        return japanese_font_path() is not None
    return False

#: Proper nouns that must NEVER be translated (guidance for translators + devs).
DO_NOT_TRANSLATE = (
    "Node Medic", "Reticulum", "Columba", "Sideband", "RNode", "LoRa", "Kin",
)

# -- internal state ---------------------------------------------------------
_catalogs: Dict[str, Dict[str, str]] = {}   # code -> {english: translation}
_current: Optional[str] = None               # cached current language code


def _catalog_path(code: str) -> str:
    return os.path.join(_I18N_DIR, f"{code}.json")


def _load_catalog(code: str) -> Dict[str, str]:
    """Load and cache a language's catalog. A missing or corrupt file yields an
    empty catalog (so every lookup falls back to the English source) — never an
    exception. English has no catalog (it IS the source)."""
    if code in _catalogs:
        return _catalogs[code]
    catalog: Dict[str, str] = {}
    if code != DEFAULT_LANGUAGE:
        try:
            with open(_catalog_path(code), encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # keep only string->string entries; ignore anything malformed
                catalog = {str(k): str(v) for k, v in data.items()
                           if isinstance(v, str)}
        except (OSError, ValueError):
            catalog = {}
    _catalogs[code] = catalog
    return catalog


def _has_catalog(code: str) -> bool:
    if code == DEFAULT_LANGUAGE:
        return True
    return os.path.exists(_catalog_path(code))


# -- persistence (plain-file pref, mirrors workflows.node_mode) -------------
def current_language() -> str:
    """The active language code, read from the pref file (cached). Defaults to
    English; an unknown/unsupported saved code also falls back to English."""
    global _current
    if _current is not None:
        return _current
    code = DEFAULT_LANGUAGE
    try:
        with open(LANGUAGE_FILE, encoding="utf-8") as f:
            saved = f.read().strip().lower()
        if saved in {c for c, _, _ in _LANGUAGES} and _is_renderable(saved):
            code = saved
    except OSError:
        code = DEFAULT_LANGUAGE
    _current = code
    return code


def set_language(code: str) -> str:
    """Persist the chosen language (applies on next app start — screens are built
    once). Unknown/unsupported codes are ignored and English is kept. Returns the
    code that ended up active."""
    code = str(code).strip().lower()
    if code not in {c for c, _, _ in _LANGUAGES} or not _is_renderable(code):
        code = DEFAULT_LANGUAGE
    try:
        os.makedirs(os.path.dirname(LANGUAGE_FILE), exist_ok=True)
        with open(LANGUAGE_FILE, "w", encoding="utf-8") as f:
            f.write(code)
    except OSError:
        pass
    global _current
    _current = code
    return code


# -- translation ------------------------------------------------------------
def tr(text: str) -> str:
    """Translate an English source string into the current language, falling back
    to the English source when there's no translation (missing key, no catalog,
    corrupt catalog, or English itself). Mechanical to apply: wrap the exact words
    already shown on screen."""
    if not text:
        return text
    code = current_language()
    if code == DEFAULT_LANGUAGE:
        return text
    return _load_catalog(code).get(text, text)


#: Conventional gettext-style alias.
_ = tr


def available_languages() -> List[Tuple[str, str, str]]:
    """Ordered (code, native_name, english_name) for every language that (a) is
    Latin-script renderable by the bundled font AND (b) English, or ships a
    catalog. This is exactly the set the picker should offer — no tofu-box
    languages, no catalog-less entries."""
    out: List[Tuple[str, str, str]] = []
    for code, native, english in _LANGUAGES:
        if not _is_renderable(code):
            continue
        if code == DEFAULT_LANGUAGE or _has_catalog(code):
            out.append((code, native, english))
    return out


def _reset_cache() -> None:
    """Test hook: drop cached catalogs + current-language so a monkeypatched pref
    path / catalog dir is re-read."""
    global _current
    _catalogs.clear()
    _current = None
