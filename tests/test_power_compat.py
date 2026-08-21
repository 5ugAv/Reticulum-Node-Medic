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


def test_zero_plus_v3_is_bench_blocked():
    """Was caution/untested until the operator's 2026-08 bench mark: same
    power class as the ruled-out V4 pairing. Bench ink overwrites hedges."""
    v = check("pi_zero_2w", "heltec32_v3")
    assert v["verdict"] == "blocked" and v["src"] == "verified"
    assert "bench" in v["why"]


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


def test_recommendations_suggest_the_SIMPLEST_pi_that_works():
    """A warning that only says 'no' strands the operator. The suggestions must
    be the smallest sufficient Pi — recommending a Pi 5 for everything reads as
    'this is expensive' when a 3 A+ would do (operator spec 2026-08-01)."""
    from workflows.power_compat import recommended_pairings, PI_POWER, check
    recs = recommended_pairings(limit=5)
    assert recs, "no workable pairings at all"
    for r in recs:
        assert check(r["pi_key"], r["board_key"])["verdict"] == "ok"
        # no SMALLER Pi also clears the bar for this board
        smaller = [k for k, v in PI_POWER.items()
                   if v["budget_ma"] < r["pi_budget"]
                   and (check(k, r["board_key"]) or {}).get("verdict") == "ok"]
        assert not smaller, f"{r['text']} — but {smaller} also works"


def test_recommendations_prefer_the_pi_the_operator_already_owns():
    from workflows.power_compat import recommended_pairings
    recs = recommended_pairings(limit=3, pi_key="pi_zero_2w")
    assert recs and all(r["pi_key"] == "pi_zero_2w" for r in recs), \
        "should offer radios for the Pi they have before a different Pi"


def test_the_operators_actual_pairing_is_flagged():
    """Zero 2 W + Heltec V3 — bench-blocked since the operator's 2026-08 mark
    (was caution while it sat untested)."""
    from workflows.power_compat import check
    v = check("pi_zero_2w", "heltec32_v3")
    assert v["verdict"] == "blocked"
    v4 = check("pi_zero_2w", "heltec32_v4")
    assert v4["verdict"] == "blocked"      # measured brownouts on this project


# --- the warning COPY (pure, so CI can check the words with no display) ----

def _lines(pi="pi_zero_2w", board="heltec32_v3"):
    from workflows.power_compat import check, warning_lines
    return warning_lines(check(pi, board), "Pi Zero 2 W", "Heltec LoRa32 V3", pi)


def test_warning_says_plainly_it_needs_a_powered_hub():
    """Operator brief 2026-08-01: the popup must state the pairing won't run
    reliably without a powered USB hub."""
    text = " ".join(l["text"] for l in _lines())
    assert "powered USB hub" in text
    assert "NOT run reliably" in text


def test_warning_explains_that_a_successful_flash_proves_nothing():
    """The trap: flashing draws less than transmitting, so a build that
    'worked' can still brown out in the field days later."""
    text = " ".join(l["text"] for l in _lines())
    assert "TRANSMITTING" in text and "brown out" in text


def test_warning_offers_a_way_forward_not_just_a_refusal():
    good = [l["text"] for l in _lines() if l["kind"] == "good"]
    assert len(good) >= 2, "a warning with no workable alternative strands the operator"
    assert any("RAK4631" in g or "T-Echo" in g for g in good)
    # suggestions must be for the Pi they already own
    assert all("Pi Zero 2 W" in g for g in good if "+" in g)


def test_warning_never_recommends_the_pairing_it_is_warning_about():
    for board in ("heltec32_v3", "heltec32_v4"):
        good = " ".join(l["text"] for l in _lines(board=board)
                        if l["kind"] == "good")
        assert "Heltec LoRa32 V" not in good, f"suggested the bad board back ({board})"


def test_blocked_and_caution_both_produce_full_advice():
    # V3 moved from caution to bench-blocked (2026-08); the LoRa32 v2.0 now
    # exercises the caution headline instead.
    for board, expect in (("heltec32_v4", "blocked"), ("lora32_v20", "may brown out")):
        ls = _lines(board=board)
        assert expect in ls[0]["text"]
        assert any(l["kind"] == "good" for l in ls)
        assert any(l["kind"] == "warn" for l in ls)


def test_every_line_has_text_and_a_known_kind():
    for l in _lines():
        assert l["text"].strip()
        assert l["kind"] in {"head", "warn", "body", "bullet", "good"}
