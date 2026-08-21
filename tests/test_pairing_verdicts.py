"""The Pi × radio bench chart — verdict levels, provenance honesty, wiring.

No Kivy imports anywhere here: the chart and the warning copy are pure, and
the screen wiring is checked by reading the source files as text.
"""

import pathlib

from workflows import pairing_verdicts as pv
from workflows.pairing_verdicts import (
    BLOCKED, CHART, COIN_FLIP, PI_KEYS, PREDICTED_FAIL, PREDICTED_PASS,
    RULED_OUT, UNTESTED, WARN_LEVELS, needs_warning, verdict)

REPO = pathlib.Path(__file__).resolve().parents[1]


# --- the keys are the CANONICAL ones, pinned to the real catalogues ---------

def test_every_chart_key_exists_in_the_real_vocabularies():
    """[[board-naming-vocabularies]]: the same board has 3+ names, so this
    chart must speak the catalogue's keys — a rename there breaks HERE."""
    from workflows.rnode_boards import RNODE_BOARDS
    from workflows.power_compat import PI_POWER
    assert set(PI_KEYS) == set(PI_POWER)
    for pi, board in CHART:
        assert pi in PI_POWER, f"unknown Pi key {pi!r}"
        assert board in RNODE_BOARDS, f"unknown board key {board!r}"


def test_the_frozen_families_cover_the_2026_08_catalogue():
    """The theory reasoned about every board known on 2026-08-22 — on the
    Zero 2W row, no catalogue board of that date is left unpredicted."""
    for board in pv._NRF52 + pv._ESP32:
        assert verdict("pi_zero_2w", board)[0] != UNTESTED, board


# --- bench ink --------------------------------------------------------------

def test_zero_v4_is_ruled_out_with_dated_bench_provenance():
    level, prov = verdict("pi_zero_2w", "heltec32_v4")
    assert level == RULED_OUT
    assert "bench (2026-08)" in prov and "Zero 2W cannot power a V4" in prov


def test_zero_v3_is_bench_blocked():
    level, prov = verdict("pi_zero_2w", "heltec32_v3")
    assert level == BLOCKED
    assert "bench (2026-08)" in prov


# --- pencil (theory 2026-08-22, unverified) ---------------------------------

def test_nrf52_boards_predicted_pass_on_every_pi():
    for board in ("rak4631", "heltec_t114", "techo"):
        for pi in PI_KEYS:
            level, prov = verdict(pi, board)
            assert level == PREDICTED_PASS, (pi, board)
            assert "unverified" in prov


def test_other_esp32_boards_predicted_fail_on_the_zero():
    for board in ("tbeam", "heltec32_v2", "lora32_v21", "t3s3", "tdeck",
                  "heltec_wireless_tracker"):
        assert verdict("pi_zero_2w", board)[0] == PREDICTED_FAIL, board


def test_xiao_on_the_zero_is_a_coin_flip():
    level, prov = verdict("pi_zero_2w", "xiao_esp32s3")
    assert level == COIN_FLIP and "coin flip" in prov


def test_charger_boards_are_coin_flips_on_the_3b_plus():
    for board in ("tbeam_supreme", "tdeck", "lora32_v21"):
        level, prov = verdict("pi_3b_plus", board)
        assert level == COIN_FLIP, board
        assert "1.2 A" in prov and "four ports" in prov


def test_pi5_passes_assume_the_5a_supply():
    # the full-current Pi 5 carries the predicted passes…
    assert verdict("pi_5_full", "heltec32_v4")[0] == PREDICTED_PASS
    assert "5 A" in verdict("pi_5_full", "heltec32_v4")[1]
    # …the 3 A row got no claim from the theory
    assert verdict("pi_5", "heltec32_v4")[0] == UNTESTED


# --- honesty: predictions must never read as facts --------------------------

def test_every_prediction_says_untested_and_every_bench_entry_is_dated():
    for (pi, board), (level, prov) in CHART.items():
        if level in (PREDICTED_PASS, PREDICTED_FAIL, COIN_FLIP):
            assert prov.startswith("Untested"), (pi, board, prov)
            assert "unverified" in prov, (pi, board, prov)
        elif level in (RULED_OUT, BLOCKED):
            assert "bench (2026-08)" in prov, (pi, board, prov)


# --- fallback + gate --------------------------------------------------------

def test_unknown_keys_fall_to_untested_honestly():
    for pair in (("pi_9000", "heltec32_v4"), ("pi_5", "mystery_board"),
                 ("", ""), ("pi_5", "")):
        level, prov = verdict(*pair)
        assert level == UNTESTED and "Untested" in prov


def test_predicted_pass_never_interrupts_but_the_rest_do():
    assert PREDICTED_PASS not in WARN_LEVELS and UNTESTED not in WARN_LEVELS
    assert not needs_warning("pi_5_full", "rak4631")
    assert needs_warning("pi_zero_2w", "heltec32_v4")      # bench ink
    assert needs_warning("pi_zero_2w", "xiao_esp32s3")     # coin flip
    assert needs_warning("pi_3b_plus", "tbeam_supreme")    # charger coin flip
    # the arithmetic can trigger it alone, chart or no chart
    assert needs_warning("pi_9000", "x", {"verdict": "blocked"})
    assert not needs_warning("pi_9000", "x", {"verdict": "ok"})


# --- the warning copy carries the chart's provenance ------------------------

def _popup_text(pi, board):
    from workflows.power_compat import check, warning_lines
    return " ".join(l["text"] for l in
                    warning_lines(check(pi, board), pi, board,
                                  pi_key=pi, board_key=board))


def test_popup_for_zero_v4_carries_the_bench_ruling():
    text = _popup_text("pi_zero_2w", "heltec32_v4")
    assert "Ruled out on the bench (2026-08)" in text
    assert "Zero 2W cannot power a V4" in text


def test_popup_for_a_chart_only_coin_flip_states_theory_not_fact():
    # check() arithmetic is content with 3B+ + T-Deck; only the chart warns —
    # and it must not promise a brown-out nobody has observed.
    text = _popup_text("pi_3b_plus", "tdeck")
    assert "coin flip" in text and "unverified" in text
    assert "NOT run reliably" not in text


def test_no_warning_copy_at_all_for_a_predicted_pass():
    from workflows.power_compat import check, warning_lines
    assert warning_lines(check("pi_5_full", "rak4631"), "Pi 5", "RAK4631",
                         pi_key="pi_5_full", board_key="rak4631") == []


# --- the screens actually consult the chart at the choosing moment ----------

def test_both_birth_flows_gate_on_the_chart():
    for rel in ("ui/screens/birth_screen.py",
                "ui/screens/birth_guide_screen.py"):
        src = (REPO / rel).read_text()
        assert "from workflows.pairing_verdicts import needs_warning" in src, rel
