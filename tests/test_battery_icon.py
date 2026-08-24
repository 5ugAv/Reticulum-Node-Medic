"""The home-screen battery gauge's pure view-model — the honesty gate.

The icon may only exist while a UPS actually answers (Medic 1 has no HAT and
must show nothing), the fill is the pack percentage and nothing else, and the
bolt is the CHARGING flag, not "plugged in looks likely".
"""

from types import SimpleNamespace as NS

from ui.battery_state import battery_view


def test_no_ups_draws_nothing():
    assert battery_view(None) is None
    assert battery_view(NS(present=False, percent=80, charging=True)) is None


def test_present_but_no_percentage_draws_nothing():
    # a UPS that answered but gave no gauge: no honest fill height exists
    assert battery_view(NS(present=True, percent=None, charging=False)) is None


def test_fraction_follows_percent():
    v = battery_view(NS(present=True, percent=75, charging=False))
    assert v == {"fraction": 0.75, "charging": False, "level": "green"}


def test_fraction_is_clamped():
    assert battery_view(NS(present=True, percent=140, charging=False))["fraction"] == 1.0
    assert battery_view(NS(present=True, percent=-5, charging=False))["fraction"] == 0.0


def test_charging_flag_carries_through():
    assert battery_view(NS(present=True, percent=50, charging=True))["charging"] is True


def test_garbage_percent_is_nothing_not_a_raise():
    assert battery_view(NS(present=True, percent="notanumber", charging=False)) is None


def test_colour_bands_green_yellow_red():
    from types import SimpleNamespace as NS
    def lvl(p):
        return battery_view(NS(present=True, percent=p, charging=False))["level"]
    assert lvl(21) == "green"
    assert lvl(20) == "yellow"     # the 15-20% caution window
    assert lvl(16) == "yellow"
    assert lvl(15) == "red"
    assert lvl(5) == "red"
