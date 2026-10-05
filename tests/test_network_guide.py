"""The Reticulum/LoRa quick-guide content module."""

from provisioning import network_guide as g


def test_the_core_concepts_are_present_in_order():
    """The three network roles first, then the two Node Medic words a newcomer
    meets on the screens — Kin and the firstborn (readiness ledger #213)."""
    terms = [t for t, _ in g.CONCEPTS]
    assert terms == ["RNode", "Transport node", "Propagation node",
                     "Kin", "The Tracker"]


def test_golden_rule_has_the_maxim():
    body = " ".join(g.GOLDEN_RULE_BODY)
    assert "If it moves, it's a passenger" in body
    assert "Transport OFF" in body


def test_role_table_covers_movers_and_fixed():
    devices = [d for d, _ in g.ROLES]
    joined = " ".join(devices).lower()
    assert "phone" in joined and "car" in joined
    assert any("rooftop" in d.lower() for d in devices)
    assert any("pi" in d.lower() for d in devices)
    # movers are peers with transport off; the fixed ones are the infrastructure
    phone_role = dict(g.ROLES)["Phone + RNode"]
    assert "Transport OFF" in phone_role


def test_the_guide_explains_the_boundary_walk():
    """Operator, 2026-09-21: the boundary walk had no entry behind the red
    "?" — the one place a person already goes to ask what something is."""
    body = " ".join(g.REACH_BODY).lower()
    assert "boundary walk" in g.REACH_TITLE.lower() + " " + body
    # what it IS: walk away and watch for the drop
    assert "walk away" in body
    # when to USE it: before committing to a site
    assert "before" in body
    # and the honest precondition the gate enforces
    assert "satellite" in body or "gps" in body


def test_the_guide_is_rendered_not_just_declared():
    """A section declared and never drawn is the project's recurring bug:
    existing is not running (WORKING_METHOD, 2026-08-18)."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "ui/widgets/guide_content.py")).read()
    assert "REACH_TITLE" in src and "REACH_BODY" in src


def test_radio_lines_match_canonical_defaults():
    lines = g.radio_lines()
    joined = " ".join(lines)
    assert "915.125 MHz" in joined
    assert "125 kHz" in joined
    assert "9" in dict(_split(l) for l in lines)["Spreading factor (SF)"]
    assert "5" in dict(_split(l) for l in lines)["Coding rate (CR)"]
    assert "17 dBm" in joined


def _split(line):
    label, _, val = line.partition(" — ")
    return label, val
