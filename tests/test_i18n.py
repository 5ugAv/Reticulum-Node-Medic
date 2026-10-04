"""i18n core tests — pure, Kivy-free (import ui.i18n only, never the screens).

Covers: translation + graceful fallback, pref round-trip on a temp path, corrupt/
missing catalog tolerance, the picker's language gating, and coverage of every
English string the vertical slice wraps against the shipped es.json.
"""

import json
import os

import pytest

from ui import i18n


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the pref file at a temp path and reset caches, so tests never touch
    the real ~/.reticulum-node-medic/language and don't leak state between them."""
    monkeypatch.setattr(i18n, "LANGUAGE_FILE", str(tmp_path / "language"))
    i18n._reset_cache()
    yield
    i18n._reset_cache()


# -- translation + fallback -------------------------------------------------

def test_tr_returns_translation_when_present():
    i18n.set_language("es")
    assert i18n.tr("Settings") == "Ajustes"


def test_tr_falls_back_to_english_source_when_key_missing():
    i18n.set_language("es")
    # A string with no catalog entry falls straight through to the English source.
    assert i18n.tr("This string is not translated anywhere") == \
        "This string is not translated anywhere"


def test_tr_is_identity_for_english():
    i18n.set_language("en")
    assert i18n.tr("Settings") == "Settings"


def test_underscore_alias_matches_tr():
    i18n.set_language("es")
    assert i18n._("About") == i18n.tr("About") == "Acerca de"


def test_tr_empty_string_is_safe():
    i18n.set_language("es")
    assert i18n.tr("") == ""


# -- persistence round-trip -------------------------------------------------

def test_set_and_current_language_round_trip():
    assert i18n.current_language() == "en"          # default with no pref file
    assert i18n.set_language("es") == "es"
    i18n._reset_cache()                             # force a re-read from disk
    assert i18n.current_language() == "es"


def test_unknown_language_falls_back_to_english():
    assert i18n.set_language("tlh") == "en"          # Klingon: no catalog/support
    assert i18n.current_language() == "en"


def test_unsupported_script_language_is_rejected():
    # Even if a code were "known", a non-Latin script must not become active.
    assert i18n.set_language("zh") == "en"


# -- corrupt / missing catalog tolerance ------------------------------------

def test_corrupt_catalog_falls_back_without_raising(tmp_path, monkeypatch):
    bad_dir = tmp_path / "i18n"
    bad_dir.mkdir()
    (bad_dir / "es.json").write_text("{ this is not valid json ", encoding="utf-8")
    monkeypatch.setattr(i18n, "_I18N_DIR", str(bad_dir))
    i18n._reset_cache()
    i18n.set_language("es")
    # No exception, and every lookup degrades to the English source.
    assert i18n.tr("Settings") == "Settings"


def test_missing_catalog_falls_back_without_raising(tmp_path, monkeypatch):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr(i18n, "_I18N_DIR", str(empty_dir))
    i18n._reset_cache()
    # 'es' has no catalog here; current_language still accepts it (it's a known,
    # Latin code) but tr() must fall back to English rather than crash.
    monkeypatch.setattr(i18n, "LANGUAGE_FILE", str(empty_dir / "language"))
    i18n.set_language("es")
    assert i18n.tr("Settings") == "Settings"


# -- available_languages gating ---------------------------------------------

def test_available_languages_includes_es_and_english():
    codes = [c for c, _n, _e in i18n.available_languages()]
    assert "es" in codes
    assert "en" in codes


def test_available_languages_includes_russian_via_dejavu():
    # Cyrillic renders under the DejaVu default font, so Russian is offered.
    codes = [c for c, _n, _e in i18n.available_languages()]
    assert "ru" in codes


def test_available_languages_excludes_scripts_without_a_font():
    codes = [c for c, _n, _e in i18n.available_languages()]
    # No font for these scripts -> must not appear (would paint tofu boxes).
    for bad in ("zh", "ar", "ko", "he"):
        assert bad not in codes


def test_japanese_gated_on_cjk_font_presence(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "LANGUAGE_FILE", str(tmp_path / "language"))
    i18n._reset_cache()
    # Without a CJK font, Japanese is hidden and cannot be activated,
    # even though it ships a full catalog.
    monkeypatch.setattr(i18n, "japanese_font_path", lambda: None)
    assert "ja" not in [c for c, _n, _e in i18n.available_languages()]
    assert i18n.set_language("ja") == "en"
    # With a Latin+CJK font present, it becomes available and selectable.
    monkeypatch.setattr(i18n, "japanese_font_path",
                        lambda: str(tmp_path / "NotoSansJP-Regular.ttf"))
    assert "ja" in [c for c, _n, _e in i18n.available_languages()]
    assert i18n.set_language("ja") == "ja"
    i18n._reset_cache()


def test_available_languages_rows_are_triples_with_names():
    for row in i18n.available_languages():
        assert len(row) == 3
        code, native, english = row
        assert code and native and english


# -- slice coverage: every wrapped English string is translated in es.json ---

#: The exact English strings wrapped in tr(...) across the vertical slice
#: (Settings, mode_toggle, VITALS, language_screen). Keep this in sync when you
#: wrap more — it guards against shipping an untranslated slice string.
WRAPPED_SLICE_STRINGS = [
    # Settings screen — title, entry titles, section headers
    "Settings", "Language", "Default radio parameters", "Tool identity",
    "Storage usage", "Trusted operators", "Date & time", "WiFi & Network",
    "Communication apps", "Reticulum & radio guide", "About",
    "Home mode", "Display", "Screen saver", "Alerts",
    "Beacon history retention", "Power",
    # mode_toggle
    "HOME",
    # VITALS filters + search
    "All", "OK", "Warn", "Alert", "Search",
    # language_screen copy
    "Choose the language Node Medic runs in.",
    ("Language set — it applies when Node Medic next starts. Slide to power off "
     "on the front page, then power back on."),
    ("Listed here: every language the display font can draw that has a full "
     "translation. More follow as translations land."),
]


def _load_shipped_catalog(code):
    path = os.path.join(i18n._I18N_DIR, f"{code}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_es_catalog_covers_every_wrapped_slice_string():
    catalog = _load_shipped_catalog("es")
    missing = [s for s in WRAPPED_SLICE_STRINGS if s not in catalog]
    assert not missing, f"es.json missing translations for: {missing}"


def test_es_catalog_is_valid_and_all_string_values():
    catalog = _load_shipped_catalog("es")
    assert catalog                                   # non-empty
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in catalog.items())


# -- broad coverage: EVERY tr(...) literal across ui/ is in es.json ----------
# AST-based so it needs no Kivy and auto-covers new wraps as they land: it walks
# the whole ui/ tree, collects every tr("literal")/_("literal") call, and asserts
# the Spanish catalog has a key for each. This is the guard that keeps coverage
# honest as more strings are wrapped (superseding the hand-listed slice above).

import ast


def _repo_root():
    return os.path.dirname(os.path.dirname(i18n._I18N_DIR))


def _wrapped_literals_in_tree(subdir):
    """Every constant string passed to tr()/_() anywhere under repo/<subdir>."""
    root = os.path.join(_repo_root(), subdir)
    found = set()
    for base, _dirs, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(base, fn)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), path)
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in ("tr", "_") and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                        and node.args[0].value):
                    found.add(node.args[0].value)
    return found


def test_every_wrapped_string_in_ui_has_es_translation():
    catalog = _load_shipped_catalog("es")
    wrapped = _wrapped_literals_in_tree("ui")
    missing = sorted(s for s in wrapped if s not in catalog)
    assert not missing, f"es.json missing {len(missing)} wrapped strings: {missing[:8]}"


# -- parity: es / fr / de must ship the SAME key set -------------------------

#: All non-English catalogs bundled under assets/i18n. Add a code here when you
#: add a language so parity + placeholder integrity are guarded for it too.
_SHIPPED_CATALOGS = ("es", "fr", "de", "sv", "pl", "id", "ru", "ja")


def test_all_catalogs_are_valid_json_string_maps():
    for code in _SHIPPED_CATALOGS:
        catalog = _load_shipped_catalog(code)
        assert catalog, f"{code}.json is empty"
        assert all(isinstance(k, str) and isinstance(v, str)
                   for k, v in catalog.items()), f"{code}.json has non-string entries"


def test_all_catalogs_have_identical_key_sets():
    es = set(_load_shipped_catalog("es"))
    for code in _SHIPPED_CATALOGS:
        other = set(_load_shipped_catalog(code))
        assert es == other, (f"{code}.json out of parity: missing "
                             f"{sorted(es - other)[:8]}, extra {sorted(other - es)[:8]}")


def test_all_catalogs_preserve_format_placeholders():
    import re
    def ph(s):
        return sorted(re.findall(r"\{[^}]*\}", s))
    for code in _SHIPPED_CATALOGS:
        catalog = _load_shipped_catalog(code)
        for src, tr in catalog.items():
            assert ph(src) == ph(tr), (
                f"{code}.json[{src!r}] placeholder mismatch: "
                f"source {ph(src)} vs translation {ph(tr)}")


def test_proper_nouns_are_not_translated_away():
    # Spot-check: a few DO_NOT_TRANSLATE proper nouns must survive verbatim in the
    # Spanish values where they appear in the key.
    catalog = _load_shipped_catalog("es")
    for key, value in catalog.items():
        for noun in ("Node Medic", "Reticulum", "RNode", "LoRa"):
            if noun in key:
                assert noun in value, f"{noun!r} translated away in es.json[{key!r}]"


# ---------------------------------------------------------------------------
# Two tiers of catalog (2026-09-03)
#
# Catalogs used to be all-or-nothing: every shipped language held to one
# identical key set. That meant a Swahili speaker could not contribute fifty
# strings and see them appear — and the languages this tool most needs are
# exactly the ones least likely to arrive complete in one go.
#
# So there are now two tiers. COMPLETE catalogs keep the parity invariant.
# IN-PROGRESS catalogs must cover the CRITICAL PATH — everything a person meets
# before they have learned anything about the tool — and past that they fall
# back to English, which the source-keyed design already does safely.
# ---------------------------------------------------------------------------

#: Partly translated, offered because they cover the first screens. Move a code
#: up into _SHIPPED_CATALOGS once it reaches full parity.
_IN_PROGRESS_CATALOGS = ("pt", "sw", "tpi")


def _critical_strings():
    from ui.i18n import critical_path
    return critical_path()


def test_the_critical_path_is_not_empty():
    """If this list ever came back empty every partial catalog would pass
    vacuously, and a language would be offered with nothing translated."""
    assert len(_critical_strings()) > 20


def test_the_critical_path_matches_the_modules_it_was_generated_from():
    """Regenerate with tools/gen_critical_path.py when a first screen changes.
    Left stale, the bar quietly stops covering what a newcomer actually sees."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tools"))
    from gen_critical_path import wrapped_strings
    assert sorted(_critical_strings()) == sorted(wrapped_strings())


@pytest.mark.parametrize("code", _IN_PROGRESS_CATALOGS)
def test_an_in_progress_catalog_covers_the_whole_critical_path(code):
    catalog = _load_shipped_catalog(code)
    missing = [s for s in _critical_strings() if s not in catalog]
    assert not missing, (
        f"{code}.json is offered but misses {len(missing)} first-contact "
        f"strings: {[m[:40] for m in missing[:4]]}")


@pytest.mark.parametrize("code", _IN_PROGRESS_CATALOGS)
def test_an_in_progress_catalog_translates_nothing_it_should_not(code):
    """Every key must be a real English source string. A typo'd key is a
    translation that silently never applies."""
    from ui.i18n import _load_catalog
    es = set(_load_shipped_catalog("es"))
    crit = set(_critical_strings())
    for key in _load_shipped_catalog(code):
        assert key in es or key in crit, f"{code}.json has stray key {key!r}"


@pytest.mark.parametrize("code", _IN_PROGRESS_CATALOGS)
def test_an_in_progress_catalog_is_a_valid_string_map(code):
    catalog = _load_shipped_catalog(code)
    assert catalog
    assert all(isinstance(k, str) and isinstance(v, str)
               for k, v in catalog.items())


@pytest.mark.parametrize("code", _IN_PROGRESS_CATALOGS)
def test_an_in_progress_catalog_keeps_its_placeholders(code):
    """{ssid}, {err}, {name} — a dropped placeholder is a crash at format time,
    in a language the person reading the traceback may not speak."""
    import re
    catalog = _load_shipped_catalog(code)
    for src, trans in catalog.items():
        assert set(re.findall(r"\{(\w+)\}", src)) == \
            set(re.findall(r"\{(\w+)\}", trans)), f"{code}: {src[:40]!r}"


def test_a_language_is_not_offered_until_it_covers_the_critical_path():
    """A language name in the picker followed by English on the very first
    screen reads as the tool being broken, not as work in progress."""
    from ui import i18n
    i18n._reset_cache()
    offered = {c for c, _n, _e in i18n.available_languages()}
    for code in _IN_PROGRESS_CATALOGS:
        assert code in offered, f"{code} covers the critical path but is hidden"
    # Hindi is registered but has no catalog yet, so it must NOT be offered.
    assert "hi" not in offered


def test_the_community_languages_are_registered():
    """Operator, 2026-09-03: the medic is for remote communities. The original
    eight are the languages of countries that buy dev boards."""
    from ui.i18n import _LANGUAGES
    codes = {c for c, _n, _e in _LANGUAGES}
    for code in ("pt", "sw", "tpi", "hi"):
        assert code in codes, f"{code} is not registered"


def test_hindi_is_gated_on_a_devanagari_font():
    """Same rule as Japanese: without the font it paints boxes, and a screen of
    boxes is worse than a screen of English."""
    from ui import i18n
    assert i18n._is_renderable("hi") == (i18n.devanagari_font_path() is not None)
