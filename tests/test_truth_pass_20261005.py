"""2026-10-05 truth pass — sentences three translation agents flagged while
wrapping them: a developer's bench voice in the radio guide, a button that
does not exist, an antenna that IS in the box, a firstborn that claimed live
satellites before it had any, an adoption 'over LoRa' that is a records entry,
and a warning translated at import before the language was known."""
import json
import os

from tests.srcutil import ROOT, func_source, src

SHIPPED = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def _catalogs():
    for code in SHIPPED:
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
            yield code, json.load(f)


def test_the_guide_names_the_button_that_exists_and_speaks_for_node_medic():
    g = src("provisioning/network_guide.py")
    assert "Boundary test" not in g and "ANTENNA ▸ Range " in g
    assert 'tr("Range test")' in src("ui/screens/triage_screen.py")
    for gone in ("On our own bench", "proven here.", "we have proven",
                 "docs/CHEAPEST_NODE.md) — but", "No antenna or case in the box"):
        assert gone not in g, gone
    assert "A small antenna is in the box; a case is not." in g
    assert "Node Medic cannot flash that build for you yet" in g
    for code, cat in _catalogs():
        assert not any("Boundary test" in k for k in cat), code
        assert not any("On our own bench" in k for k in cat), code


def test_the_cheapest_node_doc_agrees_with_the_guide():
    d = src("docs/CHEAPEST_NODE.md")
    assert "BUILD screen has no entry for a bare ESP32 yet" in d
    assert "birth it as an RNode, and provision it as a homebrew board" not in d


def test_the_firstborn_claims_nothing_it_cannot_see_yet():
    f = src("ui/firstborn_flow.py")
    assert "reporting real satellites right now" not in f
    assert "knows the time at last" not in f
    assert "As soon as it sees the sky, Node Medic knows where it stands" in f
    assert "then this begins" not in f and "then tap Begin." in f
    assert "adopted as kin over LoRa" not in src("ui/screens/birth_guide_screen.py")
    for code, cat in _catalogs():
        assert not any("reporting real satellites right now" in k for k in cat), code
        assert any(k.startswith("{name} adopted as kin — now in VITALS") for k in cat), code


def test_the_power_off_warning_is_translated_when_asked_not_at_import():
    c = src("ui/confirm.py")
    assert "FLASH_POWEROFF_WARNING = tr(" not in c
    assert "def flash_poweroff_warning() -> str:" in c
    for rel in ("ui/screens/home_screen.py", "ui/screens/settings_screen.py"):
        s = src(rel)
        assert "flash_poweroff_warning()" in s and "FLASH_POWEROFF_WARNING" not in s


def test_no_catalog_key_is_both_untranslated_everywhere_and_gone_from_the_source():
    """The 2026-10-05 cleanup removed the old guide wordings that sat in every
    catalog as English (value == key) with nothing rendering them."""
    cats = dict(_catalogs())
    never = [k for k in cats["es"] if all(cats[c].get(k) == k for c in SHIPPED)]
    import subprocess
    files = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True,
                           cwd=ROOT).stdout.split()
    blob = "\n".join(open(os.path.join(ROOT, f), encoding="utf-8", errors="replace").read()
                     for f in files)
    crit = set(json.load(open(os.path.join(ROOT, "assets/i18n/_critical.json"), encoding="utf-8")))
    from tests.test_i18n import _wrapped_literals_in_tree
    wrapped = set(_wrapped_literals_in_tree("ui"))

    def live(k):
        return (k in wrapped or k in crit or k in blob
                or (k[:30] in blob and k[-30:] in blob))
    dead = [k for k in never if not live(k)]
    assert not dead, dead[:5]
