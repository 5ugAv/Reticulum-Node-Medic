"""The recommended-hardware page — a reference a keeper acts from.

Operator, 2026-08-31: "this is probably a page that we need inside Node
Medic as recommended hardware for builds." These pins guard the three
things that make it useful rather than decorative: it is reachable from
where the question is asked, it names the expensive trap, and it does not
pretend a Pi on solar is the same job as a radio on solar.
"""

import ast
import pathlib

SRC = pathlib.Path("ui/screens/hardware_advice_screen.py").read_text()
APP = pathlib.Path("ui/app.py").read_text()
GUIDE = pathlib.Path("ui/screens/birth_guide_screen.py").read_text()


def test_page_parses_and_defines_the_class():
    classes = {n.name for n in ast.walk(ast.parse(SRC))
               if isinstance(n, ast.ClassDef)}
    assert "HardwareAdviceScreen" in classes


def test_all_three_node_kinds_are_covered():
    low = _joined().lower()
    assert "everyday node" in low          # cheapest, most numerous
    assert "remote node" in low            # nRF52 alone, battery/solar
    assert "message-holder" in low         # Pi, store-and-forward


def test_the_expensive_trap_is_named_loudly():
    # bolting a Pi to a low-power radio is the dearest build AND the
    # shortest-lived — the mistake this page exists to prevent
    joined = _joined()
    assert "DO NOT bolt a Raspberry Pi" in joined
    assert "10-20x" in joined


def _joined() -> str:
    """Source with string-literal line wrapping removed, so a pin matches the
    text the keeper READS rather than however it wrapped in the file."""
    import re
    return re.sub(r'"\s*\n\s*"', "", SRC)


def test_pi_solar_is_not_pretended_to_be_the_same_as_radio_solar():
    # "mains/solar" hid a real difference (operator's catch): a Pi on solar
    # needs a 20W panel, ~45Wh of battery and a charge controller
    joined = _joined()
    assert "20 W panel minimum" in joined
    assert "charge controller" in joined
    assert "45 Wh of battery" in joined
    assert "Pi Zero 2 W" in joined          # the only Pi to use for solar


def test_the_free_antenna_is_stated_with_its_evidence():
    joined = _joined()
    assert "8.2 cm of wire" in joined
    assert "8 cm stub beat" in joined       # our own bench, not a claim


def test_reachable_from_the_build_chooser():
    assert "_open_hardware_advice" in GUIDE
    assert "Recommended hardware" in GUIDE
    assert 'switch_mode("hardware_advice")' in GUIDE
    assert 'Screen(name="hardware_advice")' in APP
    assert "HardwareAdviceScreen()" in APP


def test_it_is_not_a_table():
    # tables are for reading and comparing; this is a page someone ACTS
    # from, and a pasted table collapses into an unreadable list
    assert "|---" not in SRC and "| " not in SRC
