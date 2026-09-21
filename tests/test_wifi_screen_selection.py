"""Settings ▸ Wi-Fi: tapping a network row must SHOW which one was tapped.
Operator, 2026-09-21: "when I click a Wi-Fi network, it doesn't highlight,
so I can't tell which one I'm selecting." A list whose selection is
invisible tells the user nothing — the row itself has to change, not just
a status line further down. Pinned in code (the Kivy screen cannot be
built headless here), the way tests/test_walk_live_check.py pins wiring."""
import re

SRC = "ui/screens/wifi_screen.py"


def _body(name):
    src = open(SRC).read()
    m = re.search(r"    def " + re.escape(name) + r"\(.*?(?=\n    def |\Z)",
                  src, re.S)
    assert m, f"{name} missing"
    return m.group(0)


def test_selecting_a_network_restyles_its_row_and_only_its_row():
    sel = _body("_select")
    assert "_highlight_row(" in sel
    hi = _body("_highlight_row")
    # the chosen row goes accent; every other row goes back to surface
    assert 'COLORS["accent"]' in hi and 'COLORS["surface"]' in hi
    assert "for " in hi, "must reset the OTHER rows, not just paint one"


def test_rows_are_remembered_so_they_can_be_restyled():
    show = _body("_show_networks")
    assert "_rows" in show
    assert "row=btn" in show or "b=btn" in show, "the row is handed to _select"
