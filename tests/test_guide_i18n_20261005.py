"""The Reticulum & radio guide (and the inline "?" help) is translated.

Readiness ledger #205: the whole guide was English in every language. The
English lives as module-level data in provisioning.network_guide; the renderer
(ui/widgets/guide_content.py) passes every string through tr() at render time,
and the shipped catalogs carry a real translation for each of them.

Pure: imports the data module and reads the catalogs. No Kivy — the renderer is
pinned by source, the way test_network_guide pins that REACH is drawn at all.
"""

import json
import os
import re

import pytest

from provisioning import network_guide as g

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE_UI = os.path.join(ROOT, "ui", "widgets", "guide_content.py")

#: The catalogs that hold the full key set (see tests/test_i18n.py).
CATALOGS = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")

#: A string this long is a sentence, not a proper noun — a catalog value equal
#: to its key there means "never translated", not "the same in both languages".
LONG = 25


def _catalog(code):
    with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def guide_strings():
    """Every English string the guide renders, in page order — the data module
    plus the one heading that lives in the renderer itself."""
    out = [g.TITLE]
    for term, body in g.CONCEPTS:
        out += [term, body]
    out.append(g.GOLDEN_RULE_TITLE)
    out += list(g.GOLDEN_RULE_BODY)
    out.append("Which device does which job?")
    for device, role in g.ROLES:
        out += [device, role]
    out.append(g.REACH_TITLE)
    out += list(g.REACH_BODY)
    out.append(g.BUILD_TITLE)
    for head, cost, lines in g.BUILD_SECTIONS:
        out += [head, cost] + list(lines)
    out += list(g.BUILD_ALWAYS)
    out.append(g.RADIO_TITLE)
    out += [label for label, _unit in g.RADIO_ROWS]
    return out


def test_the_guide_has_something_to_translate():
    strings = guide_strings()
    assert len(strings) > 40
    assert sum(1 for s in strings if len(s) > LONG) > 30


@pytest.mark.parametrize("code", CATALOGS)
def test_every_guide_string_has_a_catalog_entry(code):
    cat = _catalog(code)
    missing = [s for s in guide_strings() if s not in cat]
    assert not missing, (
        f"{code}.json lacks {len(missing)} guide strings, e.g. {missing[0][:60]!r}")


@pytest.mark.parametrize("code", CATALOGS)
def test_every_guide_sentence_is_really_translated(code):
    """The ledger's symptom: 28 keys sat in es.json with value == key — present,
    counted, and still English on the glass."""
    cat = _catalog(code)
    untranslated = [s for s in guide_strings()
                    if len(s) > LONG and cat.get(s) == s]
    assert not untranslated, (
        f"{code}.json carries {len(untranslated)} guide sentences in English, "
        f"e.g. {untranslated[0][:60]!r}")


def test_the_english_proper_nouns_survive_translation():
    """Node Medic / Reticulum / RNode / LoRa are names, not words."""
    for code in CATALOGS:
        cat = _catalog(code)
        for s in guide_strings():
            for noun in ("Node Medic", "Reticulum", "RNode", "LoRa"):
                if noun in s:
                    assert noun in cat[s], f"{code}.json[{s[:40]!r}] lost {noun}"


def test_the_renderer_translates_at_render_time():
    """Existing is not running: the catalogs could be perfect and the page
    still English if guide_content never calls tr(). Pin the call sites."""
    src = open(GUIDE_UI, encoding="utf-8").read()
    assert "from ui.i18n import tr" in src
    for call in ("tr(term)", "tr(body)", "tr(para)", "tr(device)", "tr(role)",
                 "tr(head)", "tr(cost)", "tr(ln)",
                 "tr(g.GOLDEN_RULE_TITLE)", "tr(g.REACH_TITLE)",
                 "tr(g.BUILD_TITLE)", "tr(g.RADIO_TITLE)",
                 'tr("Which device does which job?")',
                 "radio_lines(translate=tr)"):
        assert call in src, f"guide_content.py no longer renders through {call}"
    # the data module stays English: the language is chosen at runtime
    data_src = open(os.path.join(ROOT, "provisioning", "network_guide.py"),
                    encoding="utf-8").read()
    assert "from ui.i18n" not in data_src and "import tr" not in data_src


def test_the_guide_title_is_translated_where_it_is_framed():
    """The full-screen Settings entry and the "?" popup both frame the content
    with g.TITLE — in the operator's language, like the content below it."""
    for rel in ("ui/screens/guide_screen.py", "ui/widgets/help_button.py"):
        src = open(os.path.join(ROOT, rel), encoding="utf-8").read()
        assert "tr(g.TITLE)" in src, f"{rel} shows the guide title in English"


def test_radio_lines_translate_their_labels_and_keep_their_numbers():
    """The radio table is the one place the guide mixes words with live
    values: the label goes through the translator, the number does not."""
    seen = []

    def fake_tr(label):
        seen.append(label)
        return "[" + label + "]"

    lines = g.radio_lines(translate=fake_tr)
    assert seen == [label for label, _unit in g.RADIO_ROWS]
    assert lines[0].startswith("[Frequency] — ") and "MHz" in lines[0]
    joined = " ".join(lines)
    assert "915.125 MHz" in joined and "125 kHz" in joined and "17 dBm" in joined
    # and without a translator the English is unchanged — the other callers
    # (and test_network_guide) keep getting what they always got
    assert g.radio_lines()[0] == "Frequency — 915.125 MHz"


def test_the_spanish_guide_reads_in_spanish_through_tr(tmp_path, monkeypatch):
    """End to end on the pure layer: pick Spanish, ask tr() for a guide
    sentence, get Spanish back (and never the English key)."""
    from ui import i18n
    monkeypatch.setattr(i18n, "LANGUAGE_FILE", str(tmp_path / "language"))
    i18n._reset_cache()
    try:
        assert i18n.set_language("es") == "es"
        for s in guide_strings():
            if len(s) > LONG:
                out = i18n.tr(s)
                assert out != s, s[:60]
                assert re.findall(r"\{[^}]*\}", out) == re.findall(r"\{[^}]*\}", s)
        lines = g.radio_lines(translate=i18n.tr)
        assert lines[0].startswith("Frecuencia — 915.125 MHz")
    finally:
        i18n._reset_cache()
