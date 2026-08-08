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


# --- the password must never outlive one birth ----------------------------

def test_the_imager_never_caches_a_masked_field():
    """The login password was prefilled at the START OF THE NEXT BIRTH with the
    PREVIOUS node's password, in the clear (operator, 2026-08-08). Unnoticed,
    two nodes ship with the same login — and the medic deliberately does not
    store passwords, so nothing would ever surface it.

    Source inspection: the screen needs Kivy, which this suite cannot import.
    """
    from tests.srcutil import func_source
    SCREEN = "ui/screens/pi_imager_screen.py"

    field = func_source(SCREEN, "_field")
    # a masked field registers as secret, and is never restored from the cache
    assert "_secret_keys" in field
    assert "prev and not password" in field, "a password must not be refilled"

    build = func_source(SCREEN, "_build")
    assert "_secret_keys" in build, "the cache must skip secrets"
    assert "continue" in build
    assert "pop(k, None)" in build, "and actively evict any that got in"


def test_secrecy_is_not_keyed_off_the_show_hide_toggle():
    """ti.password FLIPS when the operator taps Show. Keying off it would cache
    the secret precisely when it was visible."""
    from tests.srcutil import func_source
    field = func_source("ui/screens/pi_imager_screen.py", "_field")
    secret_line = [l for l in field.splitlines() if "_secret_keys" in l
                   and "|" in l]
    assert secret_line, "expected an explicit set membership, not an attribute read"
    assert "ti.password" not in "".join(secret_line)


# --- the card must be written for the board the OPERATOR named -------------

def test_the_card_writer_uses_the_operators_choice_not_the_usb_guess():
    """USB can only name a SoC — BCM283x is a Zero 2 W, a 3A+ and a 3B+ at once
    — so the detected key is "" for all three. Handing that "" to the writer
    produced a BARE dtoverlay=dwc2 (dr_mode=otg, "read the ID pin") on a 3A+
    whose USB-A socket has no ID pin. It booted perfectly and presented nothing.
    """
    from tests.srcutil import func_source
    SCREEN = "ui/screens/pi_imager_screen.py"
    src = open(SCREEN).read()
    # the write call must not take the art key
    # scope to the write_image(...) call itself — the SurgeryAnim PORTRAIT
    # legitimately uses the detected key, because a picture is a claim about
    # the object in front of the operator.
    i = src.index("pi_imager.flash(")
    call = src[i:i + 700]                       # the call and its arguments
    assert "pi_key=self._pi_config_key()" in call, \
        f"the card writer must use the operator's chosen model, got: {call[-60:]}"
    assert "_pi_art_key" not in call

    cfg = func_source(SCREEN, "_pi_config_key")
    assert "_pi_key" in cfg, "the operator's answer comes first"
    assert "_pi_art_key" in cfg, "falling back to detection is still fine"
