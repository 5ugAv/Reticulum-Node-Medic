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


def test_last_seen_status_alert_threshold():
    # threshold widened to 18 h (3x the planned 6 h beacon cadence)
    assert theme.last_seen_status(0.5) == "ok"
    assert theme.last_seen_status(theme.NOT_HEARD_ALERT_HOURS - 0.1) == "ok"
    assert theme.last_seen_status(theme.NOT_HEARD_ALERT_HOURS + 0.1) == "alert"


def test_status_color_maps_to_palette():
    assert theme.status_color("ok") == theme.COLORS["green"]
    assert theme.status_color("warn") == theme.COLORS["amber"]
    assert theme.status_color("alert") == theme.COLORS["red"]
    # unknown -> grey/secondary
    assert theme.status_color("unknown") == theme.COLORS["text_secondary"]


def test_status_rgba_returns_tuple():
    rgba = theme.status_rgba("ok")
    assert len(rgba) == 4


# --- the phosphor palette (2026-09-29) --------------------------------------

def test_status_green_is_not_the_chrome_green():
    """A healthy node has meant green since the first screen. With the chrome
    now green too, the two must never be the same ink or the signal stops being
    a signal."""
    from ui import theme
    assert theme.COLORS["green"] != theme.COLORS["accent"]
    assert theme.COLORS["green"] != theme.COLORS["text_primary"]


def test_the_basemap_keeps_a_colour_that_reads_on_pale_tiles():
    """SCAN draws over a PALE street raster, and the operator said on
    2026-09-09 that green on it is unreadable. The blue that used to be
    `accent` survives as `map_accent` for everything drawn on those tiles."""
    from ui import theme
    from ui.screens import scan_screen          # noqa: F401 — import-time check
    assert "map_accent" in theme.COLORS
    src = open("ui/screens/scan_screen.py").read()
    i = src.index("LINK_COLOURS = {")
    block = src[i:i + 300]
    assert '"lora": "map_accent"' in block, (
        "the LoRa link lane went green — it is drawn on the pale basemap")
    assert '"accent"' not in block


def test_chrome_text_reads_on_the_chrome_ground():
    """The palette is sampled off the poster, but the pairing still has to
    work: the commonest text colour on the commonest surface."""
    from ui import theme
    def luma(h):
        r, g, b, _a = theme.hex_to_rgba(h)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    for ground in ("background", "surface", "sidebar"):
        assert luma(theme.COLORS["text_primary"]) - luma(theme.COLORS[ground]) > 0.45, (
            f"text_primary is too close to {ground}")
