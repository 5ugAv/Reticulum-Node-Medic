"""Suggesting a rebirth for a node that stopped answering (task #30).

Operator, 2026-07-31: surface rebirth as a RECOMMENDATION when a kin node is
unresponsive — "not answering — consider a rebirth if it's physically
reachable" — and offer it from node-detail for red nodes.

The machinery existed. What was missing was anything pointing at it from the
screen where an operator first notices a node is dead.
"""

from ui.rebirth_advice import advise


def test_a_healthy_node_gets_no_advice_at_all():
    """A screen must not manufacture concern about a node that is fine."""
    for ok in ("ok", "green", ""):
        assert advise(ok) is None


# --- the constraint that shapes everything ---------------------------------
# A rebirth is an esptool erase over USB. It needs the board IN HAND. A node
# quiet on a rooftop cannot be rebirthed from here, however red its dot is.

def test_a_dead_node_whose_board_is_ABSENT_gets_words_not_a_button():
    a = advise("alert", board_attached=False, name="faith")
    assert a.offer_rebirth is False
    assert "physically reach it" in a.rebirth_note
    assert "can't be done over the air" in a.rebirth_note


def test_a_dead_node_whose_board_IS_here_gets_the_offer():
    a = advise("alert", board_attached=True, name="faith")
    assert a.offer_rebirth is True
    assert any("plugged into Node Medic" in s for s in a.steps)


def test_board_attached_defaults_to_FALSE():
    """A caller that cannot tell must not get a repair button that quietly does
    nothing — the honesty gate applies to buttons as much as to demos."""
    assert advise("alert", name="x").offer_rebirth is False


# --- and it is never the first thing to try --------------------------------
# A rebirth destroys the node's identity and its certificate. A solar node
# waiting for sun is not a node that needs wiping.

def test_the_cheap_checks_come_before_the_destructive_one():
    a = advise("alert", board_attached=True, name="faith")
    assert a.steps, "no steps offered"
    joined = " ".join(a.steps)
    assert "Ping node now" in joined
    assert "solar" in joined, "a flat battery must be considered before a wipe"
    # the rebirth mention is LAST
    assert "plugged into Node Medic" in a.steps[-1]


def test_it_names_the_medics_own_radio_as_a_suspect():
    """A deaf medic makes every node look dead. Wiping a healthy node because
    the medic's own radio is at fault is the worst outcome this screen can
    cause."""
    a = advise("alert", board_attached=False, name="faith")
    assert any("Self Diagnose" in s for s in a.steps)


def test_a_warning_node_is_told_a_rebirth_is_premature():
    a = advise("warn", board_attached=True, name="faith")
    assert a.offer_rebirth is False
    assert "premature" in a.rebirth_note
    assert "still" in a.rebirth_note      # it is talking


def test_a_never_heard_node_is_not_declared_broken():
    """'unknown' means we have not heard from it YET — it may be booting."""
    a = advise("unknown", board_attached=True, name="faith")
    assert a.offer_rebirth is False
    assert "Too early" in a.rebirth_note


def test_the_node_is_named_when_we_know_it():
    assert "faith" in advise("alert", name="faith").headline
    assert advise("alert").headline          # and still says something if not


def test_quiet_hours_are_mentioned_when_known():
    a = advise("alert", name="faith", hours_quiet=52)
    assert "52 hours" in a.headline


def test_quiet_hours_are_omitted_rather_than_guessed():
    assert "hours" not in advise("alert", name="faith").headline


# --- the screen wiring ------------------------------------------------------

def test_the_detail_screen_only_offers_the_button_when_it_can_work():
    src = open("ui/screens/node_detail_screen.py").read()
    assert "self._advice.offer_rebirth" in src
    assert "self._on_rebirth is not None" in src


def test_the_button_navigates_and_does_not_erase():
    """Two confirms for one irreversible act. This page is opened just to read a
    battery level; the erase must not be one tap from that."""
    src = open("ui/screens/node_detail_screen.py").read()
    fn = src[src.index("def _rebirth"):]
    fn = fn[:fn.index("\n\n")] if "\n\n" in fn else fn
    for destructive in ("esptool", "erase", "subprocess", "wipe("):
        assert destructive not in fn, f"_rebirth performs {destructive!r} itself"
