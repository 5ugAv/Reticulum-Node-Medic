"""One board, one record — and a safe default name on rebirth (task #68).

Operator, walking a RAK4631 rebirth (2026-08-05 23:39): *"It says wiping rak3,
which is the previous name of the board. So then it says okay, start, but I'm
not prompted to change the name from rak3."*

Both branches of that lost something. Keeping the old name OVERWRITES the
previous certificate (born date, stamped location, notes) because the id derives
from the name. Choosing a new one left the old certificate behind as a stale
duplicate of a board that no longer exists.

Operator's decisions, 2026-08-07: delete the old record (one board, one record),
and default the field to the next free name rather than the old one.
"""

import json
import os

import pytest

from ui import cert_store as cs


@pytest.fixture
def certs(tmp_path):
    return str(tmp_path / "certificates")


def _save(certs, name, serial="", **extra):
    c = {"node_name": name, "usb_serial": serial}
    c.update(extra)
    return cs.save_cert(c, cert_dir=certs)


# --- one board, one record --------------------------------------------------

def test_saving_a_board_retires_its_older_record():
    """Measured live 2026-08-07: one RAK4631 serial had THREE
    certificates — rak4, zerorak, zerorak1 — for one physical board."""
    import tempfile
    d = tempfile.mkdtemp()
    _save(d, "rak3", "usb-RAKwireless_WisCore_4631000000000001-if00")
    _save(d, "rak4", "usb-RAKwireless_WisCore_4631000000000001-if00")
    names = {c["node_name"] for c in cs.load_certs(d)}
    assert names == {"rak4"}, f"old record survived: {names}"


def test_the_record_just_written_is_never_the_one_deleted():
    """The obvious way to get this wrong: retire by serial AFTER writing, and
    delete the thing you just made."""
    import tempfile
    d = tempfile.mkdtemp()
    _save(d, "solo", "usb-Thing_ABCDEF-if00")
    assert len(cs.load_certs(d)) == 1


def test_a_rename_of_the_same_board_leaves_exactly_one():
    import tempfile
    d = tempfile.mkdtemp()
    for n in ("zerorak", "zerorak1", "rak9"):
        _save(d, n, "usb-RAKwireless_WisCore_4631000000000001-if00")
    certs_ = cs.load_certs(d)
    assert len(certs_) == 1 and certs_[0]["node_name"] == "rak9"


def test_certificates_WITHOUT_a_serial_never_delete_each_other():
    """The dangerous edge. Many records carry no usb_serial — the Pi nodes and
    the older ones. If an empty serial matched, saving any one of them would
    wipe the rest, turning a tidy-up into data loss."""
    import tempfile
    d = tempfile.mkdtemp()
    _save(d, "everywhere")
    _save(d, "faith")
    _save(d, "unity")
    assert len(cs.load_certs(d)) == 3


def test_a_different_board_is_left_alone():
    import tempfile
    d = tempfile.mkdtemp()
    _save(d, "rak1", "usb-RAKwireless_WisCore_AAAA-if00")
    _save(d, "rak2", "usb-RAKwireless_WisCore_BBBB-if00")
    assert len(cs.load_certs(d)) == 2


def test_the_same_board_is_matched_across_a_vendor_case_change():
    """A RAK announces RAKWireless from its bootloader and RAKwireless once
    running — one capital apart. usb_serial_key exists for this; the retirement
    must go through it and not compare raw strings."""
    import tempfile
    d = tempfile.mkdtemp()
    _save(d, "rakA", "usb-RAKWireless_WisCore_4631000000000001-if00")
    _save(d, "rakB", "usb-RAKwireless_WisCore_4631000000000001-if00")
    assert len(cs.load_certs(d)) == 1


# --- the default in the box has to be safe when nobody reads it -------------

def test_a_trailing_number_is_incremented():
    import tempfile
    d = tempfile.mkdtemp()
    assert cs.next_free_name("rak3", cert_dir=d) == "rak4"


def test_it_skips_names_already_taken():
    import tempfile
    d = tempfile.mkdtemp()
    _save(d, "rak4", "usb-x-AAAA-if00")
    _save(d, "rak5", "usb-x-BBBB-if00")
    assert cs.next_free_name("rak3", cert_dir=d) == "rak6"


def test_a_name_with_no_number_gains_one():
    import tempfile
    d = tempfile.mkdtemp()
    assert cs.next_free_name("hope", cert_dir=d) == "hope2"


def test_no_previous_name_suggests_nothing_rather_than_inventing():
    import tempfile
    d = tempfile.mkdtemp()
    assert cs.next_free_name("", cert_dir=d) == ""


def test_the_suggestion_is_NEVER_the_old_name():
    """The whole point. Whatever else it does, it must not hand back the name
    that silently overwrites the previous certificate."""
    import tempfile
    d = tempfile.mkdtemp()
    for old in ("rak3", "hope", "node-7", "x1"):
        assert cs.next_free_name(old, cert_dir=d) != old


# --- the screen wiring ------------------------------------------------------

def test_the_wipe_path_no_longer_carries_the_old_name_forward():
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "self._node_name = old_name" not in src, (
        "the old name is being carried into the name step again")
    assert "rebirth_default_name(old_name" in src


def test_the_naming_step_shows_what_the_board_WAS():
    """As history. The operator saw 'wiping rak3' and then a box containing
    rak3, with nothing marking it as a decision still to make."""
    src = open("ui/screens/birth_guide_screen.py").read()
    assert "_rebirth_of" in src
    assert "This board was {old}" in src


# -- a board's own hash tail is kept (operator, 2026-10-03) -------------------

def test_a_hash_tail_name_is_the_boards_own_and_is_kept(tmp_path):
    """5A59 is what the RNode prints on its screen; bumping it to 5A60 made a
    node whose screen and certificate disagreed."""
    from ui.node_names import is_hash_tail_name
    assert is_hash_tail_name("5A59") and is_hash_tail_name("5ac3 ")
    assert not is_hash_tail_name("rak3") and not is_hash_tail_name("8B5") \
        and not is_hash_tail_name("5A59X") and not is_hash_tail_name("")
    d = str(tmp_path)
    assert cs.rebirth_default_name("5A59", cert_dir=d) == "5A59"
    assert cs.rebirth_default_name("5ac3", cert_dir=d) == "5AC3"
    assert cs.rebirth_default_name("rak3", cert_dir=d) == cs.next_free_name("rak3", cert_dir=d)
    assert cs.rebirth_default_name("", cert_dir=d) == ""


def test_keeping_the_boards_own_name_is_not_a_clash():
    src = open("ui/screens/birth_guide_screen.py").read()
    i = src.index("def _name_next")
    assert "same_board" in src[i:i + 2500] and "and not same_board" in src[i:i + 2500]
    assert "so it keeps the name" in src
