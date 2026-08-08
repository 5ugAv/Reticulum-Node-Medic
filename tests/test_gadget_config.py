"""The USB-gadget bake must actually be IN FORCE, not merely present.

config.txt is sectioned. A dwc2 line under [cm4]/[cm5]/[pi5] applies to those
boards and to nothing else — and Raspberry Pi OS ships one. Reading it as "this
card is already prepared" is how a Pi 3A+ came back from a birth booting
perfectly and presenting no USB device at all.
"""

# --- the [cm5] trap: a real card, 2026-08-08 -------------------------------
#
# Raspberry Pi OS ships a stock config.txt ending in board-filtered sections.
# The old idempotence check scanned every line for "dtoverlay=dwc2" with no
# regard for section, matched the [cm5] one, and declared a Pi 3A+ card already
# prepared. cmdline.txt still got its half of the edit, so the card carried
# modules-load=dwc2,g_ether with no dwc2 in the device tree: the Pi booted
# perfectly (red solid, green flashing) and presented no USB device at all.
#
# Transcribed from the actual card read out of the operator's Pi 3A+.
STOCK_TAIL = """\
arm_boost=1

[cm4]
# Enable host mode on the 2711 built-in XHCI USB controller.
# This line should be removed if the legacy DWC2 controller is required
# (e.g. for USB device mode) or if USB support is not required.
otg_mode=1

[cm5]
dtoverlay=dwc2,dr_mode=host

[pi5]
dtoverlay=nospi10

[all]
"""


def test_a_cm5_dwc2_line_does_not_count_as_prepared():
    from provisioning import gadget
    assert gadget.config_txt_has_gadget(STOCK_TAIL, "pi_3a_plus") is False


def test_the_overlay_is_actually_added_to_a_stock_card():
    from provisioning import gadget
    out = gadget.config_txt_with_gadget(STOCK_TAIL, "pi_3a_plus")
    assert out != STOCK_TAIL, "the [cm5] line must not suppress the write"
    assert gadget.config_txt_has_gadget(out, "pi_3a_plus") is True
    assert "dtoverlay=dwc2,dr_mode=peripheral" in out


def test_the_added_overlay_lands_where_it_applies():
    from provisioning import gadget
    out = gadget.config_txt_with_gadget(STOCK_TAIL, "pi_3a_plus")
    live = gadget.config_txt_applies_to_all(out)
    assert "dtoverlay=dwc2,dr_mode=peripheral" in live
    # and the stock host line stays confined to its own section
    assert "dtoverlay=dwc2,dr_mode=host" not in live


def test_our_own_line_IS_recognised_so_it_stays_idempotent():
    from provisioning import gadget
    once = gadget.config_txt_with_gadget(STOCK_TAIL, "pi_3a_plus")
    assert gadget.config_txt_with_gadget(once, "pi_3a_plus") == once


def test_a_bare_dwc2_is_not_good_enough_for_a_board_that_needs_a_mode():
    # dtoverlay=dwc2 alone leaves dr_mode=otg — "read the ID pin" — and the
    # 3A+'s USB-A socket has no ID pin. It must still be given the mode.
    from provisioning import gadget
    text = "arm_boost=1\ndtoverlay=dwc2\n"
    assert gadget.config_txt_has_gadget(text, "pi_3a_plus") is False
    out = gadget.config_txt_with_gadget(text, "pi_3a_plus")
    assert "dtoverlay=dwc2,dr_mode=peripheral" in out


def test_the_root_helper_agrees_with_the_module():
    # prepare_card.py is a standalone copy (it runs as root with no imports),
    # so the two must be checked against each other or they drift.
    import importlib.util
    import os
    from provisioning import gadget
    path = os.path.join(os.path.dirname(__file__), "..", "assets", "scripts",
                        "prepare_card.py")
    spec = importlib.util.spec_from_file_location("prepare_card", path)
    pc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pc)
    for key in ("pi_3a_plus", "pi_zero_2w", "pi_4b", ""):
        assert pc.config_txt_has_gadget(STOCK_TAIL, key) is \
               gadget.config_txt_has_gadget(STOCK_TAIL, key), key
        assert pc.config_txt_with_gadget(STOCK_TAIL, key) == \
               gadget.config_txt_with_gadget(STOCK_TAIL, key), key
