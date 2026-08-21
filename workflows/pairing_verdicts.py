"""The operator's Pi × radio bench chart, as code.

Every Pi × radio-board pairing gets bench-tested by hand, and the operator
keeps the verdicts on a paper chart. This table is that chart's code twin —
same rows, same ink, plus the power-budget PREDICTIONS (2026-08-22) pencilled
into the cells the bench hasn't reached yet.

The one rule that matters here is honesty of provenance: a bench verdict is a
FACT (the operator watched the pairing live or die, on a date); a prediction
is arithmetic about current budgets, explicitly unverified. The two must never
read the same. Every entry therefore carries its provenance text alongside its
level, and every prediction's text says so out loud ("untested — predicted
…"). A prediction displayed as a fact is exactly the kind of lie SPEC.md
forbids.

UPDATE PATH: bench results OVERWRITE predictions. When the operator tests a
pairing, replace its pencilled prediction in ``CHART`` with dated bench ink
(``RULED_OUT`` / ``BLOCKED`` / — when a pass level is finally earned — a new
bench-pass level), exactly as they do on paper. The dict is built predictions
first, bench second, so precedence is literally the build order below.

Keys are the CANONICAL ones the real flows use — the same board has three or
more names across catalogues ([[board-naming-vocabularies]]), so nothing here
invents a name: board keys come from workflows.rnode_boards.RNODE_BOARDS and
Pi keys from workflows.power_compat.PI_POWER (the vocabulary the birth
screens' PI_HOSTS dropdown already speaks). A test pins every key in this file
to those catalogues so a rename there breaks HERE, not silently on screen.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# -- verdict levels ----------------------------------------------------------
# Bench ink (operator, dated):
RULED_OUT = "ruled_out"          # tested, dead — do not build this pairing
BLOCKED = "blocked"              # operator's bench mark: not buildable as-is
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
WARN_LEVELS = frozenset({RULED_OUT, BLOCKED, PREDICTED_FAIL, COIN_FLIP})

#: The Pi vocabulary (== workflows.power_compat.PI_POWER's keys).
PI_KEYS = ("pi_zero_2w", "pi_3a_plus", "pi_3b_plus", "pi_4b",
           "pi_5", "pi_5_full")

# -- the board families the 2026-08-22 theory reasoned about -----------------
# FROZEN lists, not derived from the live catalogue: the theory covered the
# boards known on that date, and auto-extending it to a board added later
# would attribute a prediction nobody made. A new board honestly falls to
# UNTESTED until someone predicts or benches it.
_NRF52 = ("rak4631", "heltec_t114", "techo")
_ESP32 = ("lora32_v10", "lora32_v20", "lora32_v21", "tbeam", "heltec32_v2",
          "heltec32_v3", "heltec32_v4", "t3s3", "tbeam_supreme", "tdeck",
          "heltec_wireless_tracker", "xiao_esp32s3")
#: ESP32-class boards carrying an 18650 charging circuit — an attached cell
#: on charge can roughly double the draw.
_CHARGER_18650 = ("tbeam_supreme", "tdeck", "lora32_v21")

_THEORY = "power-budget theory 2026-08-22, unverified"

#: (pi_key, board_key) -> (level, provenance_text). Provenance is a full
#: sentence, display-ready: bench entries name the bench and the date,
#: predictions name the theory and say "untested".
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
# 18650-charger boards on the Pi 3B+: the charger can add ~0.5 A and the
# 3B+'s 1.2 A budget is shared across all four USB ports.
for _b in _CHARGER_18650:
    CHART[("pi_3b_plus", _b)] = (
        COIN_FLIP,
        f"Untested — a coin flip ({_THEORY}): an 18650 cell on charge can "
        "add ~0.5 A, and the Pi 3B+'s 1.2 A USB budget is shared across all "
        "four ports.")
# Pi 5 passes ASSUME the 5 A supply (pi_5_full); the 3 A row stays untested —
# the theory made no claim about a 600 mA budget feeding an ESP32 board.
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
CHART[("pi_zero_2w", "heltec32_v3")] = (
    BLOCKED,
    "Blocked on the bench (2026-08): the operator's mark — same power class "
    "as the ruled-out V4 pairing.")


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

    True when the chart carries bench ink or a fail/coin-flip pencil mark, OR
    when power_compat's arithmetic (*power_verdict*, as returned by
    ``workflows.power_compat.check``) says blocked/caution. One pure predicate
    so both birth flows gate identically and CI can test the gate without a
    display.
    """
    if (power_verdict or {}).get("verdict") in ("blocked", "caution"):
        return True
    return verdict(pi_key, radio_key)[0] in WARN_LEVELS
