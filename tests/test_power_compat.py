"""Pi <-> radio-board power compatibility guide."""

from workflows.power_compat import check, BOARD_POWER, PI_POWER, OVERRIDES
from workflows.rnode_boards import official_boards, custom_boards


def test_every_pickable_board_has_a_power_entry():
    for b in official_boards() + custom_boards():
        assert b.key in BOARD_POWER, f"no power data for {b.key}"


def test_zero_plus_v4_is_blocked_by_field_verification():
    v = check("pi_zero_2w", "heltec32_v4")
    assert v["verdict"] == "blocked" and v["src"] == "verified"
    assert "browns out" in v["why"]
    assert any("POWERED USB hub" in r for r in v["remedies"])
    assert any("17 dBm" in r for r in v["remedies"])       # lower-TX remedy


def test_zero_plus_v3_is_untested_caution():
    v = check("pi_zero_2w", "heltec32_v3")
    assert v["verdict"] == "caution" and v["src"] == "untested"
    assert "bench-tested" in v["why"]


def test_low_power_boards_pass_everywhere_even_the_zero():
    for board in ("rak4631", "techo", "heltec_t114"):
        for pi in PI_POWER:
            assert check(pi, board)["verdict"] == "ok", (pi, board)


def test_default_pi5_blocks_the_v4_but_full_current_allows_it():
    assert check("pi_5", "heltec32_v4")["verdict"] == "blocked"
    ok = check("pi_5_full", "heltec32_v4")
    assert ok["verdict"] == "ok"
    # the default-Pi5 remedy points at the current flag we ship in setup_boot
    v = check("pi_5", "heltec32_v4")
    assert any("usb_max_current_enable" in r for r in v["remedies"])


def test_unknown_pair_returns_none():
    assert check("pi_9000", "heltec32_v3") is None
    assert check("pi_5", "mystery_board") is None


def test_remedies_suggest_lighter_boards_for_a_weak_pi():
    v = check("pi_zero_2w", "tbeam")
    assert v["verdict"] in ("caution", "blocked")
    assert any("lower-power board" in r for r in v["remedies"])


def test_3a_plus_powers_the_v4_field_verified():
    # arithmetic alone says "thin margin"; the bench says it runs cleanly —
    # field-verified overrides beat estimates.
    v = check("pi_3a_plus", "heltec32_v4")
    assert v["verdict"] == "ok" and v["src"] == "verified"
