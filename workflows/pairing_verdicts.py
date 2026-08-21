"""The operator's Pi × radio bench chart, as code.

Every Pi × radio-board pairing gets bench-tested by hand, and the operator
keeps the verdicts on a paper chart. This table is that chart's code twin —
same rows, same ink, plus the power-budget PREDICTIONS (2026-08-22) pencilled
into the cells the bench hasn't reached yet.

The one rule that matters here is honesty of provenance: a bench verdict is a
FACT (the operator watched the pairing live or die, on a date); a prediction
is arithmetic about current budgets, explicitly unverified; and a chart mark
nobody has explained is exactly that — a mark, not a verdict. The three must
never read the same. Every entry therefore carries its provenance text
alongside its level, and every prediction's text says so out loud ("untested
— predicted …"). A prediction displayed as a fact is exactly the kind of lie
SPEC.md forbids — and so is a secondhand reading of the operator's pen: the
Zero 2W + V3 cell below was once encoded here as "blocked on the bench" from
an unconfirmed reading of a chart mark, while ui/birth_guide_flow.py records
that exact pairing PROVEN end-to-end, hub-free (HOPE, 2026-08-01). It now
carries OPERATOR_MARK_UNCONFIRMED: warn, cite both facts, decide nothing.

UPDATE PATH: bench results OVERWRITE predictions. When the operator tests a
pairing, replace its pencilled prediction in ``CHART`` with dated bench ink
(``RULED_OUT`` / ``BLOCKED`` / — when a pass level is finally earned — a new
bench-pass level), exactly as they do on paper. The dict is built pencil
first, ink second, so precedence is literally the build order below. An
unconfirmed mark resolves the same way: ask the operator what it meant, then
replace it with the real level.

Keys are the CANONICAL ones the real flows use — the same board has three or
more names across catalogues ([[board-naming-vocabularies]]), so nothing here
invents a name: board keys come from workflows.rnode_boards.RNODE_BOARDS and
Pi keys from workflows.power_compat.PI_POWER (the vocabulary the birth
screens' PI_HOSTS dropdown already speaks, and that provisioning/pi_model.py
detection yields — note detection never yields "pi_5_full", so the plain
"pi_5" row is the one live hardware actually hits). A test pins every key in
this file to those catalogues so a rename there breaks HERE, not silently on
screen.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from workflows.power_compat import check as _arith_check

# -- verdict levels ----------------------------------------------------------
# Bench ink (operator, dated):
RULED_OUT = "ruled_out"          # tested, dead — do not build this pairing
BLOCKED = "blocked"              # operator's bench mark: not buildable as-is
# A mark on the paper chart whose MEANING nobody has confirmed with the
# operator — surfaced as a soft warning, never treated as a bench verdict:
OPERATOR_MARK_UNCONFIRMED = "operator_mark_unconfirmed"
# Pencil (power-budget theory 2026-08-22, unverified):
PREDICTED_FAIL = "predicted_fail"
COIN_FLIP = "coin_flip"
PREDICTED_PASS = "predicted_pass"
# No ink, no pencil:
UNTESTED = "untested"

#: Levels worth interrupting an operator for at the choosing moment. A
#: predicted PASS is deliberately not here — an unverified "should work" is
#: not a warning, and nagging on it would train people to tap past the real
#: ones. It still answers honestly through verdict() if anything asks.
WARN_LEVELS = frozenset({RULED_OUT, BLOCKED, OPERATOR_MARK_UNCONFIRMED,
                         PREDICTED_FAIL, COIN_FLIP})

#: The Pi vocabulary (== workflows.power_compat.PI_POWER's keys).
PI_KEYS = ("pi_zero_2w", "pi_3a_plus", "pi_3b_plus", "pi_4b",
           "pi_5", "pi_5_full")

# -- the board families the 2026-08-22 theory reasoned about -----------------
# FROZEN lists, not derived from the live catalogue: the theory covered the
# boards known on that date, and auto-extending it to a board added later
# would attribute a prediction nobody made. A new board honestly falls to
# UNTESTED until someone predicts or benches it. (A test compares the union
# of these families against the frozen catalogue so drift is caught, not
# papered over.)
_NRF52 = ("rak4631", "heltec_t114", "techo")
_ESP32 = ("lora32_v10", "lora32_v20", "lora32_v21", "tbeam", "heltec32_v2",
          "heltec32_v3", "heltec32_v4", "t3s3", "tbeam_supreme", "tdeck",
          "heltec_wireless_tracker", "xiao_esp32s3")
#: Boards whose charging surcharge is DOCUMENTED in the repo's own power
#: table: exactly the BOARD_POWER entries carrying the "+~500 mA if a LiPo is
#: attached and charging" note (tbeam, tbeam_supreme). A test derives this
#: set from those notes so the two can't drift apart.
_CHARGER_DOCUMENTED = ("tbeam", "tbeam_supreme")
#: Boards the 2026-08-22 theory ALSO called charger coin-flips, but whose
#: charging draw is documented nowhere in this repo — the surcharge is an
#: assumption, and their provenance text says so.
_CHARGER_ASSUMED = ("tdeck", "lora32_v21")

_THEORY = "power-budget theory 2026-08-22, unverified"

#: (pi_key, board_key) -> (level, provenance_text). Provenance is a full
#: sentence, display-ready: bench entries name the bench and the date,
#: predictions name the theory and say "untested", unconfirmed marks say
#: "unconfirmed".
CHART: Dict[Tuple[str, str], Tuple[str, str]] = {}

# --- pencil layer: the 2026-08-22 power-budget predictions ------------------
# nRF52 boards peak under 0.15 A — inside every Pi's USB budget.
for _b in _NRF52:
    for _p in PI_KEYS:
        CHART[(_p, _b)] = (PREDICTED_PASS,
                           f"Untested — predicted to pass ({_THEORY}): nRF52 "
                           "boards peak under 0.15 A, inside every Pi's USB "
                           "budget.")
# Every other ESP32-class board is predicted to brown the Zero 2W's rail…
for _b in _ESP32:
    CHART[("pi_zero_2w", _b)] = (PREDICTED_FAIL,
                                 f"Untested — predicted to fail ({_THEORY}): "
                                 "ESP32-class TX spikes of ~0.5 A+ brown the "
                                 "Zero 2W's rail.")
# …except the XIAO ESP32S3, which sits right on the line.
CHART[("pi_zero_2w", "xiao_esp32s3")] = (
    COIN_FLIP,
    f"Untested — a genuine coin flip ({_THEORY}): the XIAO ESP32S3 sits "
    "right at the Zero 2W's limit.")
# 18650/LiPo-charger boards on the Pi 3B+: a cell on charge adds draw, and
# the 3B+'s 1.2 A budget is shared across all four USB ports. Two grades of
# evidence, two provenance texts — documented surcharge vs assumed.
for _b in _CHARGER_DOCUMENTED:
    CHART[("pi_3b_plus", _b)] = (
        COIN_FLIP,
        f"Untested — a coin flip ({_THEORY}): the power table documents "
        "~+500 mA on this board when a cell is charging, and the Pi 3B+'s "
        "1.2 A USB budget is shared across all four ports.")
for _b in _CHARGER_ASSUMED:
    CHART[("pi_3b_plus", _b)] = (
        COIN_FLIP,
        f"Untested — a coin flip ({_THEORY}): this board's charging draw is "
        "ASSUMED (documented nowhere in the power table), against the Pi "
        "3B+'s 1.2 A USB budget shared across all four ports.")
# The plain Pi 5 — the key detection actually yields — on a 3 A supply caps
# USB at 600 mA: tight against ESP32-class spikes. Cells are added only where
# the current arithmetic is silent ("ok"); where check() already says
# caution/blocked (T-Beam-class, V4) the popup fires without our help and a
# duplicate pencil mark would just say the same thing twice.
for _b in _ESP32:
    _a = _arith_check("pi_5", _b)
    if _a is not None and _a.get("verdict") != "ok":
        continue
    CHART[("pi_5", _b)] = (
        COIN_FLIP,
        f"Untested — a coin flip ({_THEORY}): a 3 A supply caps the Pi 5's "
        "USB at 600 mA, tight against ESP32-class TX spikes. A 5 A supply "
        "(usb_max_current_enable=1) lifts the budget to 1.6 A.")
# The full-current Pi 5 predictions ASSUME the 5 A supply.
for _b in _ESP32:
    CHART[("pi_5_full", _b)] = (
        PREDICTED_PASS,
        f"Untested — predicted to pass ({_THEORY}): assumes the 5 A supply, "
        "whose 1.6 A USB budget covers ESP32-class spikes.")

# --- ink layer: bench verdicts overwrite the pencil -------------------------
CHART[("pi_zero_2w", "heltec32_v4")] = (
    RULED_OUT,
    "Ruled out on the bench (2026-08): the Zero 2W cannot power a V4 — its "
    "rail cannot feed the V4's spikes (operator's bench ruling).")
# NOT ink: the paper chart carries a mark on Zero 2W + V3, but its meaning
# was never confirmed with the operator — and the repo holds dated evidence
# pointing the other way (HOPE, the flagship cable birth, was Zero 2W + V3,
# proven end-to-end with no hub on 2026-08-01 — ui/birth_guide_flow.py).
# Both facts go to the operator; neither gets promoted to a verdict here.
CHART[("pi_zero_2w", "heltec32_v3")] = (
    OPERATOR_MARK_UNCONFIRMED,
    "The operator's paper chart carries a mark on this cell (2026-08); its "
    "meaning is unconfirmed — and the repo records this exact pairing PROVEN "
    "end-to-end, hub-free (HOPE, 2026-08-01).")


def verdict(pi_key: str, radio_key: str) -> Tuple[str, str]:
    """The chart's answer for one cell: ``(level, provenance_text)``.

    Unknown keys — a Pi or board this chart has never heard of — fall to
    UNTESTED, because "I don't know" is the only honest answer for a cell
    that isn't on the paper.
    """
    return CHART.get((pi_key, radio_key),
                     (UNTESTED, "Untested — no bench result and no "
                                "prediction for this pairing yet."))


def needs_warning(pi_key: str, radio_key: str,
                  power_verdict: Optional[dict] = None) -> bool:
    """Should the choosing moment interrupt the operator about this pairing?

    True when the chart carries bench ink, an unconfirmed operator mark, or a
    fail/coin-flip pencil mark, OR when power_compat's arithmetic
    (*power_verdict*, as returned by ``workflows.power_compat.check``) says
    blocked/caution. One pure predicate so both birth flows gate identically
    and CI can test the gate without a display. warning_lines() gates on the
    same verdict() lookup, so a True here always has copy to show.
    """
    if (power_verdict or {}).get("verdict") in ("blocked", "caution"):
        return True
    return verdict(pi_key, radio_key)[0] in WARN_LEVELS
