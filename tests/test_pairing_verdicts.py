"""The Pi × radio bench chart — verdict levels, provenance honesty, wiring.

No Kivy window anywhere here: the chart and the warning copy are pure, and
the screen methods are exercised the house way (tests/srcutil.func_source:
compile the ACTUAL shipped method, run it against a stub self).
"""

import sys
import textwrap
import types

from tests.srcutil import func_source, src

from workflows import pairing_verdicts as pv
from workflows.pairing_verdicts import (
    BLOCKED, CHART, COIN_FLIP, OPERATOR_MARK_UNCONFIRMED, PI_KEYS,
    PREDICTED_FAIL, PREDICTED_PASS, RULED_OUT, UNTESTED, WARN_LEVELS,
    needs_warning, verdict)

SCREEN = "ui/screens/birth_screen.py"


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


#: The board catalogue as of the 2026-08-22 freeze. The families are frozen
#: lists (a later board must not inherit a prediction nobody made), so this
#: guard holds them against the catalogue OF THAT DATE: grow the catalogue
#: and this stays green (new board -> honest UNTESTED); lose or rename a key
#: and it breaks loudly.
_FREEZE_2026_08 = {
    "lora32_v10", "lora32_v20", "lora32_v21", "tbeam", "heltec32_v2",
    "heltec32_v3", "heltec32_v4", "t3s3", "rak4631", "techo",
    "tbeam_supreme", "tdeck", "heltec_t114", "xiao_esp32s3",
    "heltec_wireless_tracker",
}


def test_family_freeze_covers_the_2026_08_catalogue_exactly():
    from workflows.rnode_boards import RNODE_BOARDS
    union = set(pv._NRF52) | set(pv._ESP32)
    assert union == _FREEZE_2026_08, (
        "family lists drifted from the 2026-08-22 freeze: "
        f"missing={_FREEZE_2026_08 - union} extra={union - _FREEZE_2026_08}")
    assert _FREEZE_2026_08 <= set(RNODE_BOARDS), (
        "frozen keys no longer exist in the catalogue: "
        f"{_FREEZE_2026_08 - set(RNODE_BOARDS)}")
    assert not (set(pv._NRF52) & set(pv._ESP32))


# --- bench ink --------------------------------------------------------------

def test_zero_v4_is_ruled_out_with_dated_bench_provenance():
    level, prov = verdict("pi_zero_2w", "heltec32_v4")
    assert level == RULED_OUT
    assert "bench (2026-08)" in prov and "Zero 2W cannot power a V4" in prov


def test_zero_v3_is_an_unconfirmed_mark_not_a_verdict():
    """The paper chart carries a mark on this cell, but its meaning was never
    confirmed — and ui/birth_guide_flow.py records the SAME pairing proven
    end-to-end, hub-free (HOPE, 2026-08-01). Neither fact gets promoted."""
    level, prov = verdict("pi_zero_2w", "heltec32_v3")
    assert level == OPERATOR_MARK_UNCONFIRMED
    assert "unconfirmed" in prov
    assert "HOPE, 2026-08-01" in prov
    # the HOPE evidence really is in the repo where the module says it is
    assert "Pi Zero 2 W +" in src("ui/birth_guide_flow.py")
    # and power_compat's arithmetic cell is back to exactly main's hedge
    from workflows.power_compat import check
    v = check("pi_zero_2w", "heltec32_v3")
    assert v["verdict"] == "caution" and v["src"] == "untested"


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


def test_documented_chargers_derive_from_the_power_tables_own_notes():
    """The charger list is the repo's own data, not a hand-list: exactly the
    BOARD_POWER entries whose note documents charging draw."""
    from workflows.power_compat import BOARD_POWER
    documented = {k for k, b in BOARD_POWER.items()
                  if "charging" in b.get("note", "")}
    assert set(pv._CHARGER_DOCUMENTED) == documented
    for board in pv._CHARGER_DOCUMENTED:
        level, prov = verdict("pi_3b_plus", board)
        assert level == COIN_FLIP, board
        assert "documents" in prov and "1.2 A" in prov and "four ports" in prov


def test_assumed_chargers_admit_the_assumption_in_their_provenance():
    from workflows.power_compat import BOARD_POWER
    for board in pv._CHARGER_ASSUMED:
        assert "charging" not in BOARD_POWER[board].get("note", ""), (
            f"{board} now has a documented charging note - move it to "
            "_CHARGER_DOCUMENTED")
        level, prov = verdict("pi_3b_plus", board)
        assert level == COIN_FLIP, board
        assert "ASSUMED" in prov


def test_the_detected_pi5_key_carries_the_3a_claims():
    """Detection yields pi_5, never pi_5_full — so the plain row must hold
    the theory's 600 mA claim or no live Pi 5 would ever see it."""
    from provisioning.pi_model import key_from_model
    from workflows.power_compat import check
    assert key_from_model("Raspberry Pi 5 Model B Rev 1.0") == "pi_5"
    # cells exist only where the arithmetic is silent…
    level, prov = verdict("pi_5", "heltec32_v3")
    assert level == COIN_FLIP and "600 mA" in prov
    # …and where check() already warns, no duplicate pencil mark
    assert check("pi_5", "heltec32_v4")["verdict"] == "blocked"
    assert verdict("pi_5", "heltec32_v4")[0] == UNTESTED
    assert check("pi_5", "tbeam_supreme")["verdict"] == "caution"
    assert verdict("pi_5", "tbeam_supreme")[0] == UNTESTED


def test_pi5_cells_actually_fire_on_the_detected_key_across_the_matrix():
    from workflows.power_compat import check
    for board in pv._ESP32:
        assert needs_warning("pi_5", board, check("pi_5", board)), board
    for board in pv._NRF52:
        assert not needs_warning("pi_5", board, check("pi_5", board)), board


def test_pi5_full_predictions_still_assume_the_5a_supply():
    level, prov = verdict("pi_5_full", "heltec32_v4")
    assert level == PREDICTED_PASS and "5 A" in prov


# --- honesty: predictions and marks must never read as facts ----------------

def test_provenance_wording_matches_its_evidence_grade():
    for (pi, board), (level, prov) in CHART.items():
        if level in (PREDICTED_PASS, PREDICTED_FAIL, COIN_FLIP):
            assert prov.startswith("Untested"), (pi, board, prov)
            assert "unverified" in prov, (pi, board, prov)
        elif level == OPERATOR_MARK_UNCONFIRMED:
            assert "unconfirmed" in prov, (pi, board, prov)
            assert "PROVEN" in prov, (pi, board, prov)   # cites the counter-evidence
        elif level in (RULED_OUT, BLOCKED):
            assert "bench (2026-08" in prov, (pi, board, prov)


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
    assert needs_warning("pi_zero_2w", "heltec32_v3")      # unconfirmed mark
    assert needs_warning("pi_zero_2w", "xiao_esp32s3")     # coin flip
    assert needs_warning("pi_3b_plus", "tbeam")            # charger coin flip
    # the arithmetic can trigger it alone, chart or no chart
    assert needs_warning("pi_9000", "x", {"verdict": "blocked"})
    assert not needs_warning("pi_9000", "x", {"verdict": "ok"})


def test_a_true_gate_always_has_copy_to_show():
    """needs_warning and warning_lines share one verdict() lookup — an
    interrupt with a blank popup would strand the operator."""
    from workflows.rnode_boards import RNODE_BOARDS
    from workflows.power_compat import check, warning_lines
    for pi in list(PI_KEYS) + ["", "pi_9000"]:
        for board in list(RNODE_BOARDS) + ["", "mystery_board"]:
            v = check(pi, board)
            if needs_warning(pi, board, v):
                assert warning_lines(v, pi or "?", board or "?",
                                     pi_key=pi, board_key=board), (pi, board)


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


def test_popup_for_zero_v3_shows_the_mark_and_the_hope_evidence():
    text = _popup_text("pi_zero_2w", "heltec32_v3")
    assert "unconfirmed" in text and "HOPE, 2026-08-01" in text


def test_popup_for_a_chart_only_coin_flip_states_theory_not_fact():
    # check() arithmetic is content with 3B+ + T-Beam; only the chart warns —
    # and it must not promise a brown-out nobody has observed.
    text = _popup_text("pi_3b_plus", "tbeam")
    assert "coin flip" in text and "unverified" in text
    assert "NOT run reliably" not in text
    # the alternatives heading admits its own evidence grade
    assert "on paper" in text


def test_no_warning_copy_at_all_for_a_predicted_pass():
    from workflows.power_compat import check, warning_lines
    assert warning_lines(check("pi_5_full", "rak4631"), "Pi 5", "RAK4631",
                         pi_key="pi_5_full", board_key="rak4631") == []


# --- the screens actually consult the chart at the choosing moment ----------

def test_both_birth_flows_gate_on_the_chart():
    for rel in ("ui/screens/birth_screen.py",
                "ui/screens/birth_guide_screen.py"):
        assert "from workflows.pairing_verdicts import needs_warning" in src(rel), rel


def test_birth_screen_gate_survives_a_broken_chart_module():
    """'Never block a birth' norm: the chart import sits in a try/except whose
    fallback is the old arithmetic-only gate."""
    start = textwrap.dedent(func_source(SCREEN, "_start_build"))
    i = start.index("needs_warning")
    assert "except Exception" in start[i:]
    assert '("blocked", "caution")' in start[i:]   # the fallback gate


# --- _power_banner: the chart can never withhold the card-write button ------
#
# birth_screen._build_chooser withholds "Set up this Pi's card" exactly when
# _power_banner() returns True, and _power_banner consults ONLY
# power_compat.check — so a chart level (unconfirmed mark, coin flip) can warn
# but can never reach that blocked path. Exercised the house way: compile the
# shipped method, run it with a stub self (Kivy can't open a window in CI).

def _run_power_banner(monkeypatch, pi_key, board_key):
    source = textwrap.dedent(func_source(SCREEN, "_power_banner"))
    ns = {"PI_HOSTS": [(pi_key, pi_key)]}
    exec(compile(source, SCREEN, "exec"), ns)
    callout = types.ModuleType("ui.widgets.callout")
    callout.Callout = lambda text: ("callout", text)
    monkeypatch.setitem(sys.modules, "ui.widgets.callout", callout)
    added = []
    stub = types.SimpleNamespace(
        _sel_pi=(pi_key, pi_key),
        _sel_board=types.SimpleNamespace(key=board_key, display_name=board_key),
        header=types.SimpleNamespace(add_widget=added.append))
    return ns["_power_banner"](stub), added


def test_power_banner_blocked_verdict_withholds_the_card_write(monkeypatch):
    blocked, added = _run_power_banner(monkeypatch, "pi_zero_2w", "heltec32_v4")
    assert blocked is True and added        # banner shown, and…
    # …the caller's blocked branch returns before offering the card write
    chooser = func_source(SCREEN, "_build_chooser")
    i = chooser.index("blocked = self._power_banner()")
    j = chooser.index("Set up this Pi's card")
    assert i < j
    branch = chooser[i:j]
    assert "if blocked:" in branch and "return" in branch


def test_unconfirmed_mark_can_never_reach_the_blocked_path(monkeypatch):
    # _power_banner never consults the chart, structurally…
    banner_src = func_source(SCREEN, "_power_banner")
    assert "pairing_verdicts" not in banner_src
    # …and behaviourally: every OPERATOR_MARK_UNCONFIRMED cell leaves the
    # card-write button in place (returns False), with the caution banner up.
    marks = [k for k, (lvl, _) in CHART.items()
             if lvl == OPERATOR_MARK_UNCONFIRMED]
    assert marks, "no unconfirmed-mark cells left to guard"
    for pi_key, board_key in marks:
        blocked, added = _run_power_banner(monkeypatch, pi_key, board_key)
        assert blocked is False, (pi_key, board_key)
        assert added, "the banner must still warn"    # button + banner


def test_recommendations_never_offer_a_cell_the_chart_warns_about():
    """The recommender and the popup must agree: suggesting a pairing whose
    own selection would warn contradicts the tool one tap later."""
    from workflows.power_compat import recommended_pairings
    for pi in PI_KEYS:
        for r in recommended_pairings(limit=10, pi_key=pi):
            assert verdict(r["pi_key"], r["board_key"])[0] not in WARN_LEVELS, r
