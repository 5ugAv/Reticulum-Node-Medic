"""Pi ↔ radio-board power compatibility — the BIRTH eligibility guide.

Can this Pi actually POWER that radio board over USB? Radio boards draw in
bursts (LoRa TX, WiFi, GPS acquisition, boot inrush), and an under-provisioned
Pi browns the board out mid-operation — the classic "it flashes fine on my
laptop but dies on the Pi" failure.

Numbers are worst-case burst draw at the 5 V USB side, tagged by provenance:
  measured  — anchored on hardware we have exercised (the Heltec V4's brownout
              history on this very project) or vendor-stated Pi budgets
  estimate  — datasheet-derived, deliberately conservative; the medic should
              tighten these as bench tests happen (calibrate, don't assume)

verdicts: ok (>=25% headroom) / caution (fits, thin margin or untested combo)
/ blocked (will brown out). Every non-ok verdict carries plain-English remedies
for the popup: powered hub, a Pi that can, a lighter board, the Pi 5 current
flag, or turning the TX power down.
"""

from __future__ import annotations

from typing import Dict, List, Optional

HEADROOM = 1.25          # "ok" needs budget >= peak * this

#: Radio boards: worst-case burst draw (mA @5V) + provenance + notes.
BOARD_POWER: Dict[str, dict] = {
    "lora32_v21":      {"peak_ma": 450, "src": "estimate"},
    "lora32_v20":      {"peak_ma": 450, "src": "estimate"},
    "lora32_v10":      {"peak_ma": 450, "src": "estimate"},
    "tbeam":           {"peak_ma": 550, "src": "estimate",
                        "note": "add ~500 mA if a LiPo is attached and charging"},
    "heltec32_v2":     {"peak_ma": 450, "src": "estimate"},
    "heltec32_v3":     {"peak_ma": 450, "src": "estimate"},
    "heltec32_v4":     {"peak_ma": 900, "src": "measured",
                        "note": "28 dBm PA; brownouts observed on this project "
                                "(flash-erase glitches, Pi Zero TX brownout)"},
    "t3s3":            {"peak_ma": 450, "src": "estimate",
                        "note": "SX1280-PA variant draws more (~550 mA)"},
    "rak4631":         {"peak_ma": 150, "src": "estimate"},   # nRF52, no WiFi
    "techo":           {"peak_ma": 150, "src": "estimate"},
    "tbeam_supreme":   {"peak_ma": 550, "src": "estimate",
                        "note": "add ~500 mA if a LiPo is attached and charging"},
    "tdeck":           {"peak_ma": 550, "src": "estimate"},
    "heltec_t114":     {"peak_ma": 160, "src": "estimate"},
    "xiao_esp32s3":    {"peak_ma": 400, "src": "estimate"},
    "heltec_wireless_tracker": {"peak_ma": 450, "src": "estimate",
                                "note": "SX1262 TX + GNSS + TFT concurrently"},
}

#: Pi models: continuous USB output budget (mA, total across ports).
PI_POWER: Dict[str, dict] = {
    "pi_zero_2w": {"budget_ma": 500, "src": "estimate",
                   "note": "OTG port, no per-port limiter, poor burst "
                           "tolerance — treat as 500 mA"},
    "pi_3a_plus": {"budget_ma": 1000, "src": "estimate",
                   "note": "single USB-A on a 2.5 A supply"},
    "pi_3b_plus": {"budget_ma": 1200, "src": "measured",
                   "note": "1.2 A shared across all four ports"},
    "pi_4b":      {"budget_ma": 1200, "src": "measured",
                   "note": "1.2 A shared across all four ports"},
    "pi_5":       {"budget_ma": 600, "src": "measured",
                   "note": "600 mA total on a 3 A supply"},
    "pi_5_full":  {"budget_ma": 1600, "src": "measured",
                   "note": "5 A supply or usb_max_current_enable=1 "
                           "(the medic's own boot config sets this)"},
}

#: Combination overrides — field truth beats arithmetic.
OVERRIDES: Dict[tuple, dict] = {
    ("pi_zero_2w", "heltec32_v4"): {
        "verdict": "blocked", "src": "verified",
        "why": "A Pi Zero cannot power the Heltec V4 - it browns out on "
               "transmit (confirmed on hardware)."},
    ("pi_3a_plus", "heltec32_v4"): {
        "verdict": "ok", "src": "verified",
        "why": "Pi 3A+ powers the Heltec V4 cleanly (confirmed on hardware "
               "2026-07-18)."},
    ("pi_zero_2w", "heltec32_v3"): {
        "verdict": "caution", "src": "untested",
        "why": "Pi Zero + Heltec V3 is close to the Zero's limit and has not "
               "been bench-tested yet - verify before deploying."},
}


def _remedies(pi_key: str, board_key: str, peak: int) -> List[str]:
    out = ["Use a POWERED USB hub between the Pi and the board."]
    fits = [PI_POWER[k] for k in ("pi_3a_plus", "pi_4b", "pi_5_full")
            if PI_POWER[k]["budget_ma"] >= peak * HEADROOM]
    if pi_key == "pi_5":
        out.append("Enable full USB current on this Pi 5 (5 A supply + "
                   "usb_max_current_enable=1) - lifts the budget to 1.6 A.")
    elif fits:
        out.append("Use a Pi with more USB power (Pi 3A+/4/5-with-5A-supply).")
    lighter = sorted((k for k, b in BOARD_POWER.items()
                      if b["peak_ma"] * HEADROOM <=
                      PI_POWER[pi_key]["budget_ma"]),
                     key=lambda k: BOARD_POWER[k]["peak_ma"])
    if lighter:
        out.append("Or choose a lower-power board this Pi can run: "
                   + ", ".join(lighter[:3]) + ".")
    if peak >= 700:
        out.append("Or configure a lower TX power at birth - at 17 dBm this "
                   "board draws far less than at full PA output.")
    return out


#: Human names for the recommendation list (the UI's own labels live in
#: ui.screens.birth_screen; these keep this module standalone/testable).
_PI_NAMES = {"pi_zero_2w": "Pi Zero 2 W", "pi_3a_plus": "Pi 3 A+",
             "pi_3b_plus": "Pi 3 B+", "pi_4b": "Pi 4 B",
             "pi_5": "Pi 5 (3 A)", "pi_5_full": "Pi 5 (5 A)"}
_BOARD_NAMES = {"lora32_v21": "LilyGO LoRa32 v2.1", "lora32_v20": "LilyGO LoRa32 v2.0",
                "lora32_v10": "LilyGO LoRa32 v1.0", "tbeam": "LilyGO T-Beam",
                "heltec32_v2": "Heltec LoRa32 v2", "heltec32_v3": "Heltec LoRa32 v3",
                "heltec32_v4": "Heltec LoRa32 v4", "t3s3": "LilyGO T3S3",
                "rak4631": "RAK4631", "techo": "LilyGO T-Echo",
                "tbeam_supreme": "LilyGO T-Beam Supreme", "tdeck": "LilyGO T-Deck",
                "heltec_t114": "Heltec Mesh Node T114",
                "xiao_esp32s3": "Seeed XIAO ESP32S3",
                "heltec_wireless_tracker": "Heltec Wireless Tracker"}


def recommended_pairings(limit: int = 4, pi_key: str = "") -> List[dict]:
    """Pi + board combinations that power cleanly with real headroom — what to
    suggest when the operator's chosen pair is blocked or marginal.

    Computed from the same numbers as ``check`` (never hard-coded advice, so it
    can't drift), ranked by MARGIN so the sturdiest pairing leads. When
    *pi_key* is given, its own workable boards come first — an operator who
    already owns that Pi would rather change the radio than the computer
    (operator spec 2026-08-01).
    """
    # For each board, the SMALLEST Pi that still clears the headroom bar. A
    # recommendation should be the simplest thing that works, not the biggest
    # Pi in the catalogue — an operator reads "you need a Pi 5" as "this is
    # expensive", when a 3 A+ would have done.
    from workflows.pairing_verdicts import verdict as _chart_verdict, \
        WARN_LEVELS as _CHART_WARNS
    best = {}
    for bk, board in BOARD_POWER.items():
        for pk, pi in sorted(PI_POWER.items(), key=lambda kv: kv[1]["budget_ma"]):
            v = check(pk, bk)
            if not v or v.get("verdict") != "ok":
                continue
            # The bench chart vetoes too: recommending a cell the tool would
            # itself warn about (a Pi-5-at-3A coin flip, say) is advice that
            # contradicts its own popup one tap later.
            if _chart_verdict(pk, bk)[0] in _CHART_WARNS:
                continue
            best[bk] = {
                "pi_key": pk, "board_key": bk,
                "pi": _PI_NAMES.get(pk, pk),
                "board": _BOARD_NAMES.get(bk, bk),
                "margin_ma": pi["budget_ma"] - board["peak_ma"],
                "pi_budget": pi["budget_ma"],
                "same_pi": pk == pi_key,
                "text": f"{_PI_NAMES.get(pk, pk)} + {_BOARD_NAMES.get(bk, bk)}",
            }
            break                       # smallest sufficient Pi wins
    out = list(best.values())
    # the operator's OWN Pi first (changing the radio is cheaper than the
    # computer), then the simplest builds
    out.sort(key=lambda r: (not r["same_pi"], r["pi_budget"], -r["margin_ma"]))
    return out[:limit]


def check(pi_key: str, board_key: str) -> Optional[dict]:
    """Verdict for powering *board_key* from *pi_key*'s USB:
    {verdict: ok|caution|blocked, why, remedies, src}. None = unknown pair."""
    pi = PI_POWER.get(pi_key)
    board = BOARD_POWER.get(board_key)
    if pi is None or board is None:
        return None
    over = OVERRIDES.get((pi_key, board_key))
    if over:
        v = dict(over)
        if v["verdict"] != "ok":
            v["remedies"] = _remedies(pi_key, board_key, board["peak_ma"])
        return v
    peak, budget = board["peak_ma"], pi["budget_ma"]
    if budget >= peak * HEADROOM:
        return {"verdict": "ok", "src": board["src"],
                "why": f"{budget} mA available vs ~{peak} mA peak draw."}
    if budget >= peak:
        return {"verdict": "caution", "src": board["src"],
                "why": f"Fits, but the margin is thin ({budget} mA available "
                       f"vs ~{peak} mA peak) - bursts may brown out.",
                "remedies": _remedies(pi_key, board_key, peak)}
    return {"verdict": "blocked", "src": board["src"],
            "why": f"This Pi cannot power this board: ~{peak} mA peak draw vs "
                   f"{budget} mA available - it will brown out.",
            "remedies": _remedies(pi_key, board_key, peak)}


#: Short names for warnings. "Heltec LoRa32 v4" is the catalogue name; on a
#: warning the operator is scanning, "Heltec V4" is what they call it and what
#: is silkscreened on the board (operator, 2026-08-02: "don't even put the
#: LoRa32 in").
_BOARD_SHORT = {
    "heltec32_v2": "Heltec V2", "heltec32_v3": "Heltec V3",
    "heltec32_v4": "Heltec V4", "heltec_t114": "Heltec T114",
    "heltec_wireless_tracker": "Heltec Tracker",
    "lora32_v21": "LoRa32 v2.1", "lora32_v20": "LoRa32 v2.0",
    "lora32_v10": "LoRa32 v1.0", "t3s3": "T3-S3", "tbeam": "T-Beam",
    "tbeam_supreme": "T-Beam Supreme", "tdeck": "T-Deck",
    "techo": "T-Echo", "rak4631": "RAK4631", "xiao_esp32s3": "XIAO S3",
}


def short_board_name(board_key: str, fallback: str = "") -> str:
    """What the operator calls this board. Falls back to the catalogue name."""
    return _BOARD_SHORT.get(board_key, fallback or _BOARD_NAMES.get(board_key, board_key))


def warning_lines(verdict: dict, pi_name: str, board_name: str,
                  pi_key: str = "", board_key: str = "") -> List[dict]:
    """The power-warning copy, as ordered ``{text, kind}`` lines.

    Pure so the WORDS can be tested without a display — the medic's Kivy popup
    just renders whatever this returns. ``kind`` picks the colour:
    ``head`` / ``warn`` / ``body`` / ``bullet`` / ``good``.

    The operator's brief (2026-08-01): say plainly that the pairing will not run
    reliably without a powered USB hub, and then point at combinations that
    need no hub at all — most people don't own one, and a node that needs one
    permanently isn't a simple build.

    With *board_key*, the operator's bench chart (workflows.pairing_verdicts)
    speaks too: bench ink as a warn line, pencil predictions as a plain body
    line — the wording itself keeps fact and theory apart. When the chart
    warns but the arithmetic said "ok" (a coin-flip cell), the chart line IS
    the message: no "will not run reliably" claim gets made, because nobody
    has verified one.
    """
    from workflows.pairing_verdicts import (verdict as chart_verdict,
                                            WARN_LEVELS)
    chart = None                       # the chart's provenance line, if any
    # Same gate as pairing_verdicts.needs_warning, on purpose: verdict()
    # answers UNTESTED for empty/unknown keys, so an empty key can never make
    # the two disagree (needs_warning saying "interrupt" while this returns
    # nothing would leave the operator staring at a blank popup).
    level, prov = chart_verdict(pi_key, board_key)
    if level in WARN_LEVELS:
        # kind "body" on purpose: both screens render body lines, and the
        # provenance WORDING already carries the weight (bench vs theory).
        chart = {"kind": "body", "text": prov}
    v = (verdict or {}).get("verdict", "")
    if v not in ("blocked", "caution"):
        # Arithmetic is content, so only the chart has something to say. A
        # coin flip is not a brown-out promise — state the cell and stop.
        if chart is None:
            return []
        lines = [{"kind": "head",
                  "text": f"{pi_name} + {board_name}: untested pairing."},
                 chart]
        recs = recommended_pairings(limit=3, pi_key=pi_key)
        if recs:
            # "on paper": these come from the estimated draw figures in
            # BOARD_POWER, not from bench runs — the heading says so.
            lines.append({"kind": "good",
                          "text": "Pairings within budget on paper "
                                  "(estimated draw figures):"})
            lines += [{"kind": "good",
                       "text": f"  ✓ {r['text']}   "
                               f"({r['margin_ma']} mA to spare)"}
                      for r in recs]
        return lines
    headline = "blocked" if v == "blocked" else "may brown out"
    lines = [
        {"kind": "head",
         "text": f"The {pi_name} may not power the {board_name} over USB "
                 f"— {headline}."},
    ]
    if (verdict or {}).get("why"):
        lines.append({"kind": "body", "text": verdict["why"]})
    # The chart line always shows alongside the arithmetic's why — no dedup.
    # A substring-match dedup was tried and dropped: it keyed on the text's
    # leading phrase, which silently breaks the moment either sentence is
    # reworded (brittle, and it fails open by HIDING provenance). Two sources,
    # two sentences is the honest rendering even when they overlap.
    if chart is not None:
        lines.append(chart)
    lines.append({"kind": "warn", "text":
                  f"This pairing will NOT run reliably without a powered USB "
                  f"hub between the Pi and the {board_name}. Note the flash "
                  f"itself may well succeed — a radio draws its peak when "
                  f"TRANSMITTING, so an under-powered node can brown out days "
                  f"later in the field."})
    for rem in (verdict or {}).get("remedies", [])[:3]:
        lines.append({"kind": "bullet", "text": "  \u2022 " + rem})
    recs = recommended_pairings(limit=3, pi_key=pi_key)
    if recs:
        lines.append({"kind": "good",
                      "text": "Combinations that run without a powered hub:"})
        for r in recs:
            lines.append({"kind": "good",
                          "text": f"  \u2713 {r['text']}   "
                                  f"({r['margin_ma']} mA to spare)"})
    return lines
