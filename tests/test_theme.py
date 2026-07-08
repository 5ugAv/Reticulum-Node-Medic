import pytest

from ui import theme


def test_palette_has_all_named_colours():
    for name in (
        "background", "surface", "sidebar", "green", "amber", "red",
        "accent", "text_primary", "text_secondary",
    ):
        assert name in theme.COLORS
        assert theme.COLORS[name].startswith("#")


def test_hex_to_rgba_full_alpha():
    r, g, b, a = theme.hex_to_rgba("#00c853")
    assert a == 1.0
    assert 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0
    # #00c853 -> green channel is the strongest
    assert g > r and g > b


def test_hex_to_rgba_black_and_white():
    assert theme.hex_to_rgba("#000000") == (0.0, 0.0, 0.0, 1.0)
    assert theme.hex_to_rgba("#ffffff") == (1.0, 1.0, 1.0, 1.0)


def test_hex_to_rgba_custom_alpha():
    assert theme.hex_to_rgba("#000000", 0.5)[3] == 0.5


def test_battery_status_thresholds():
    assert theme.battery_status(100) == "ok"
    assert theme.battery_status(21) == "ok"
    assert theme.battery_status(20) == "warn"
    assert theme.battery_status(11) == "warn"
    assert theme.battery_status(10) == "alert"
    assert theme.battery_status(3) == "alert"


def test_signal_status_thresholds():
    assert theme.signal_status(-90) == "ok"
    assert theme.signal_status(-110) == "warn"
    assert theme.signal_status(-115) == "warn"
    assert theme.signal_status(-120) == "alert"
    assert theme.signal_status(-130) == "alert"


def test_last_seen_status_six_hour_rule():
    assert theme.last_seen_status(0.5) == "ok"
    assert theme.last_seen_status(5.9) == "ok"
    assert theme.last_seen_status(6.1) == "alert"


def test_status_color_maps_to_palette():
    assert theme.status_color("ok") == theme.COLORS["green"]
    assert theme.status_color("warn") == theme.COLORS["amber"]
    assert theme.status_color("alert") == theme.COLORS["red"]
    # unknown -> grey/secondary
    assert theme.status_color("unknown") == theme.COLORS["text_secondary"]


def test_status_rgba_returns_tuple():
    rgba = theme.status_rgba("ok")
    assert len(rgba) == 4
