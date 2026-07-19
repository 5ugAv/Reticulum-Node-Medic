"""Front-page poster tap zones — pure geometry."""

from ui.home_zones import zone_at, CARD_ORDER, CARDS_TOP


def test_five_cards_left_to_right():
    y = 0.9                                   # inside the card row
    hits = [zone_at(0.04 + 0.92 * (i + 0.5) / 5, y) for i in range(5)]
    assert hits == ["vitals", "scan", "birth", "triage", "probe"]
    assert hits == CARD_ORDER


def test_card_row_edges_and_margins():
    assert zone_at(0.05, CARDS_TOP + 0.01) == "vitals"
    assert zone_at(0.95, 0.99) == "probe"
    assert zone_at(0.01, 0.9) is None          # left margin
    assert zone_at(0.99, 0.9) is None          # right margin
    assert zone_at(0.5, CARDS_TOP - 0.02) is None   # just above the cards


def test_red_cross_opens_mitosis():
    assert zone_at(0.50, 0.46) == "mitosis"    # dead centre of the emblem
    assert zone_at(0.44, 0.42) == "mitosis"    # inside the circle
    assert zone_at(0.50, 0.10) is None         # up in the mesh art
    assert zone_at(0.15, 0.46) is None         # off to the side


def test_out_of_image_taps_are_none():
    assert zone_at(-0.1, 0.5) is None
    assert zone_at(0.5, 1.2) is None
