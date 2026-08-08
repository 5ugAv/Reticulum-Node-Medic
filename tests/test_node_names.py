"""A node name already in the family must be flagged before the birth runs.

Operator, mid-walkthrough 2026-08-08, at "Step 1 of 5 — Name this node":
"user should be warned if they use the name of an existing node in the family".

Two nodes with one name is not cosmetic. The name is what shows on the SCAN
map, in VITALS, on the certificate, and in a repair conversation months later
when whoever built it has moved away. Two "solarlove"s send a crew to the wrong
roof.
"""

import json
import os

from ui import node_names


def _cert_dir(tmp_path, *names):
    d = tmp_path / "certs"
    d.mkdir()
    for i, n in enumerate(names):
        (d / f"c{i}.json").write_text(json.dumps(
            {"id": f"c{i}", "node_name": n, "type": "rnode"}))
    return str(d)


def _roster(tmp_path, *names):
    p = tmp_path / "kin.json"
    p.write_text(json.dumps(
        {f"hash{i}": {"name": n} for i, n in enumerate(names)}))
    return str(p)


# --- the two registers ----------------------------------------------------

def test_a_name_born_here_clashes(tmp_path):
    cd = _cert_dir(tmp_path, "solarlove")
    assert "solarlove" in node_names.clash("solarlove", cd, _roster(tmp_path))


def test_a_name_only_on_the_map_clashes(tmp_path):
    # Nodes ADOPTED rather than built here live only in the kin roster. Checking
    # certificates alone would miss exactly the nodes someone else built — the
    # ones a name clash is most likely to confuse.
    cd = _cert_dir(tmp_path)
    assert node_names.clash("faith", cd, _roster(tmp_path, "faith"))


def test_a_free_name_does_not_clash(tmp_path):
    cd = _cert_dir(tmp_path, "solarlove")
    assert node_names.clash("rooftop-east", cd, _roster(tmp_path, "faith")) == ""


def test_the_message_says_where_it_is_used(tmp_path):
    cd = _cert_dir(tmp_path, "hope")
    msg = node_names.clash("hope", cd, _roster(tmp_path, "hope"))
    assert "born here" in msg and "on the map" in msg


# --- how names compare ----------------------------------------------------

def test_case_and_spacing_do_not_hide_a_clash(tmp_path):
    cd = _cert_dir(tmp_path, "Solar Love")
    for typed in ("solarlove", "SOLARLOVE", "Solar  Love", " solar love "):
        assert node_names.clash(typed, cd, _roster(tmp_path)), typed


def test_an_empty_name_is_never_a_clash(tmp_path):
    cd = _cert_dir(tmp_path, "solarlove")
    for empty in ("", "   ", None):
        assert node_names.clash(empty, cd, _roster(tmp_path)) == ""


def test_a_blank_stored_name_does_not_swallow_every_lookup(tmp_path):
    # A cert with no name must not register as the empty key and then match.
    cd = _cert_dir(tmp_path, "", "solarlove")
    assert node_names.clash("anything", cd, _roster(tmp_path)) == ""
    assert node_names.clash("solarlove", cd, _roster(tmp_path))


# --- it advises; it must never break a birth ------------------------------

def test_missing_registers_are_not_an_error(tmp_path):
    missing_dir = str(tmp_path / "nope")
    missing_roster = str(tmp_path / "nope.json")
    assert node_names.clash("anything", missing_dir, missing_roster) == ""
    assert node_names.existing_names(missing_dir, missing_roster) == {}


def test_corrupt_registers_are_not_an_error(tmp_path):
    d = tmp_path / "certs"
    d.mkdir()
    (d / "broken.json").write_text("{not json at all")
    bad_roster = tmp_path / "kin.json"
    bad_roster.write_text("also not json")
    assert node_names.clash("anything", str(d), str(bad_roster)) == ""


def test_existing_names_reports_both_sources(tmp_path):
    cd = _cert_dir(tmp_path, "hope")
    names = node_names.existing_names(cd, _roster(tmp_path, "faith"))
    assert names["hope"] == ["born here"]
    assert names["faith"] == ["on the map"]


# --- the screen's warn-then-allow --------------------------------------------
#
# Checked by SOURCE INSPECTION, not by importing the screen. Kivy is not
# importable in this suite (CI has none, and other tests install partial stubs
# that make `import ui.screens.birth_guide_screen` raise
# "TypeError: 'type' object is not iterable" from the import machinery itself,
# but only when those tests run first). The same convention the SD-handover and
# birth-guide suites already use.

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_guide_screen.py"


def _name_next_src():
    return func_source(SCREEN, "_name_next")


def test_the_name_step_consults_the_clash_check():
    src = _name_next_src()
    assert "node_names" in src and "clash" in src


def test_a_clash_redraws_the_step_instead_of_advancing():
    src = _name_next_src()
    warn = src[src.index("if msg:"):]
    assert "_render_name()" in warn, "must redraw carrying the warning"
    assert "return" in warn, "must not fall through to _render_step"
    assert "_node_name = name" in warn, "what they typed must survive the redraw"


def test_the_warning_is_remembered_so_a_second_tap_goes_ahead():
    # Reusing a name can be deliberate — rebuilding a node that died, keeping
    # its place on the map — so the tool warns, it does not refuse.
    src = _name_next_src()
    assert "_name_warned" in src
    assert 'name != getattr(self, "_name_warned", None)' in src


def test_a_failing_lookup_cannot_block_a_birth():
    # The check is advice. If it throws, the birth proceeds.
    src = _name_next_src()
    assert "except Exception" in src
    assert 'msg = ""' in src


def test_an_empty_name_is_still_rejected_before_any_of_this():
    src = _name_next_src()
    assert src.index("if not name:") < src.index("clash")


def test_the_button_changes_when_the_warning_is_showing():
    # A second identical "Next →" would give the operator no sign that their
    # tap was heard, or that the next one means something different.
    src = func_source(SCREEN, "_render_name")
    assert "_name_warning" in src and "Use it anyway" in src
