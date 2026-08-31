"""Recommended hardware — the build advice a keeper acts from.

Operator, 2026-08-31: "this is probably a page that we need inside Node
Medic as recommended hardware for builds", then — seeing a second button
appear next to the help icon — "there is already the question mark icon in
the corner of Birth. maybe adding that information there is useful."

So it lives in the ONE guide the "?" already opens, not in a competing
button. These pins guard what makes it useful rather than decorative.
"""

import pathlib

from provisioning import network_guide as g

GUIDE_UI = pathlib.Path("ui/widgets/guide_content.py").read_text()
CHOOSER = pathlib.Path("ui/screens/birth_guide_screen.py").read_text()


def _all_text() -> str:
    parts = [g.BUILD_TITLE] + list(g.BUILD_ALWAYS)
    for head, cost, lines in g.BUILD_SECTIONS:
        parts += [head, cost] + list(lines)
    return "\n".join(parts)


def test_all_three_node_kinds_are_covered():
    low = _all_text().lower()
    assert "everyday node" in low          # cheapest, most numerous
    assert "remote node" in low            # nRF52 alone, battery/solar
    assert "message-holder" in low         # Pi, store-and-forward


def test_the_expensive_trap_is_named_loudly():
    # bolting a Pi to a low-power radio is the dearest build AND the
    # shortest-lived — the mistake this advice exists to prevent
    text = _all_text()
    assert "DO NOT bolt a Raspberry Pi" in text
    assert "10\u201320\u00d7" in text or "10-20x" in text


def test_pi_solar_is_not_pretended_to_be_the_same_as_radio_solar():
    # "mains/solar" hid a real difference (operator's catch): a Pi on solar
    # needs a 20W panel, ~45Wh of battery and a charge controller
    text = _all_text()
    assert "20 W panel minimum" in text
    assert "charge controller" in text
    assert "45 Wh of battery" in text
    assert "Pi Zero 2 W" in text           # the only Pi to use for solar


def test_the_free_antenna_is_stated_with_its_evidence():
    text = _all_text()
    assert "8.2 cm of wire" in text
    assert "8 cm stub beat" in text        # our own bench, not a claim


def test_it_lives_behind_the_EXISTING_help_icon():
    # one help affordance, not two: the guide renders it, and the chooser
    # grew no competing button
    assert "BUILD_SECTIONS" in GUIDE_UI and "BUILD_ALWAYS" in GUIDE_UI
    assert "Recommended hardware" not in CHOOSER
    assert "hardware_advice" not in CHOOSER


def test_the_costs_are_present_so_it_can_be_planned_against():
    text = _all_text()
    assert "AU$7" in text and "AU$30" in text and "AU$60" in text
