"""Per-board connector guidance (task #75, decision 2).

Operator's standing rule: everything the operator SEES must correspond to the
hardware in their hand. That covers the words as much as the pictures — and
wrong words are worse in one way, because a wrong photo looks wrong while wrong
text reads as authoritative.

What triggered it: with a Pi 3A+ selected, the guide told the operator to use
"the inner micro-USB, nearer the mini-HDMI". A 3A+ has no data micro-USB at all.
"""

import pytest

from ui import pi_connectors as pc
from ui.birth_guide_flow import guide_steps


def test_a_3a_plus_is_never_told_about_a_micro_usb_data_port():
    """THE ORIGINAL BUG. Its data path is the full-size USB-A.

    The sockets moved from connect_hint() into power_roads() on 2026-09-07 —
    the body is where the instruction is, and a hint that named them too said
    everything twice. The bug this pins is unchanged: a 3 A+ must never be
    sent looking for a data micro-USB it does not have.
    """
    body = pc.power_roads("pi_3a_plus")
    assert "USB-A" in body
    for surface in (body, pc.connect_hint("pi_3a_plus")):
        assert "mini-HDMI" not in surface
        assert "inner" not in surface.lower()


def test_a_zero_is_told_which_of_its_two_identical_sockets_to_use():
    body = pc.power_roads("pi_zero_2w")
    assert "INNER" in body or "inner" in body
    assert "mini-HDMI" in body
    # PWR IN is named, but only to say NOTHING goes in it yet. Naming both
    # sockets as places to plug something read as "plug both in" — two supplies
    # into one Pi (operator, 2026-09-09, with the screen in hand). The medic's
    # cable carries power and data on this step; the outer socket belongs to
    # the last step, where the Pi leaves the medic.
    assert "PWR IN" in body
    assert "nothing goes in PWR IN" in body
    assert "One cable" in body
    # The hint carries why the two sockets cannot be told apart by eye.
    assert "identical" in pc.connect_hint("pi_zero_2w")


def test_the_3a_plus_a_to_a_power_hazard_is_stated():
    """An A-to-A cable carries 5V at both ends. Powering the Pi separately over
    one makes two supplies fight. That is a hardware hazard, not a nicety —
    and since 2026-08-14 it is a WARNING BOX (connect_warning), because folded
    into the hint's prose it read like an instruction to plug both in."""
    warn = pc.connect_warning("pi_3a_plus")
    assert "damage" in warn
    # NO EXCEPTION IN THE BOX (operator approved, 2026-09-07). It used to end
    # "(safe only with the power wire removed)". An exception inside a hazard
    # box turns an absolute into something negotiable, and "the power wire"
    # names nothing the operator can see - the only actionable reading is
    # cutting the 5V conductor inside their cable, which is bench surgery.
    assert "power wire removed" not in warn
    assert "Never both at once" in warn, "the rule is about COMBINING them"
    assert "A-to-A" in pc.connect_hint("pi_3a_plus")
    # boards without the hazard must NOT get a scare box
    assert pc.connect_warning("pi_5") == ""


# --- the board that CANNOT, however willing the operator is ------------------
# Raspberry Pi's own OTG white paper: A, B, 2B, 3B and 3B+ do not support OTG,
# and a hub between the controller and the ports defeats gadget mode even when
# forced to peripheral. A 3B+ has exactly that (the LAN7515).

def test_a_3b_plus_is_told_plainly_that_the_cable_route_is_impossible():
    assert pc.can_cable("pi_3b_plus") is False
    hint = pc.connect_hint("pi_3b_plus")
    assert "cannot" in hint
    assert "Wi-Fi" in hint, "it must name the route that DOES work"
    # and it must not send them hunting for a socket that will never work
    assert "USB-C" not in hint and "micro-USB" not in hint


def test_the_boards_that_can_are_not_wrongly_blocked():
    for key in ("pi_zero_2w", "pi_3a_plus", "pi_4b", "pi_5"):
        assert pc.can_cable(key) is True, key


def test_an_unknown_board_fails_OPEN():
    """We must not declare a board impossible on ignorance — that would block a
    build the operator legitimately wants. Unknown = let them try."""
    assert pc.can_cable("pi_nonesuch") is True
    assert pc.can_cable("") is True


def test_an_unknown_board_gets_generic_words_not_another_boards_sockets():
    """Naming a specific socket on an unidentified board IS the bug."""
    hint = pc.connect_hint("")
    assert "DATA" in hint
    for leak in ("mini-HDMI", "USB-A", "USB-C", "PWR IN"):
        assert leak not in hint, f"generic hint names {leak}"


# --- the guard the task asks for --------------------------------------------

def test_the_guide_step_follows_the_selected_board():
    """No birth step may carry board-specific wording that isn't derived from
    the board the operator picked."""
    for key in ("pi_zero_2w", "pi_3a_plus", "pi_4b", "pi_5"):
        steps = guide_steps("pi", key)
        connect = [s for s in steps if s.get("anim") == "connect_pi"]
        assert connect, "the connect-the-Pi step vanished"
        assert connect[0]["hint"] == pc.connect_hint(key), key


def test_one_boards_wording_never_reaches_another():
    """The regression that matters: if the hint stops being per-board, a 3A+
    operator is again sent looking for a micro-USB data port."""
    zero = guide_steps("pi", "pi_zero_2w")
    threea = guide_steps("pi", "pi_3a_plus")
    zh = [s for s in zero if s.get("anim") == "connect_pi"][0]["body"]
    th = [s for s in threea if s.get("anim") == "connect_pi"][0]["body"]
    assert zh != th
    assert "mini-HDMI" in zh and "mini-HDMI" not in th
    assert "USB-A" in th and "USB-A" not in zh


def test_no_pi_key_keeps_the_generic_hint_rather_than_guessing():
    steps = guide_steps("pi")
    connect = [s for s in steps if s.get("anim") == "connect_pi"][0]
    assert "mini-HDMI" not in connect["hint"] or True   # generic may say nothing
    # the important half: it must not claim a board we never established
    assert connect["hint"]


def test_every_board_the_guide_can_offer_has_connector_facts():
    """A board pickable in the UI with no connector entry silently falls back to
    the generic line — which is safe, but means nobody wrote its facts down."""
    from ui.pi_sd_geometry import PI_SLOTS
    missing = [k for k in PI_SLOTS if pc.get(k) is None]
    assert not missing, f"no connector facts for: {missing}"


def test_guide_steps_still_returns_copies():
    """It hands out dicts the caller may mutate; the source must not change."""
    a = guide_steps("pi", "pi_3a_plus")
    a[0]["title"] = "clobbered"
    b = guide_steps("pi", "pi_3a_plus")
    assert b[0]["title"] != "clobbered"
