"""The Reticulum/LoRa quick-guide content module."""

from provisioning import network_guide as g


def test_three_core_concepts_present():
    terms = [t for t, _ in g.CONCEPTS]
    assert terms == ["RNode", "Transport node", "Propagation node"]


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
