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


def test_available_languages_excludes_unsupported_script():
    codes = [c for c, _n, _e in i18n.available_languages()]
    # No CJK / RTL / Cyrillic codes may appear while the font is Latin-only.
    for bad in ("zh", "ar", "ja", "ru", "ko", "he"):
        assert bad not in codes


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
    "Language set — it applies when Node Medic restarts.",
    ("More languages will follow. The current display font renders Latin scripts "
     "only (Spanish, French, German, Portuguese, Italian, Indonesian)."),
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
_SHIPPED_CATALOGS = ("es", "fr", "de", "sv", "pl")


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
