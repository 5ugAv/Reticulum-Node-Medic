"""UI design system — palette, colour helpers and status thresholds.

Pure data / functions with no Kivy dependency so it can be unit-tested and
reused by non-graphical code (e.g. the monitor backend deciding a node's
status colour).
"""

from __future__ import annotations

import math
from typing import Tuple

#: 1280x720 landscape dark theme palette.
COLORS = {
    "background": "#1a1a1a",
    "surface": "#242424",
    "sidebar": "#141414",
    "green": "#00c853",
    "amber": "#ff6d00",
    "red": "#d50000",
    "accent": "#4fc3f7",   # steel blue
    # Link-overlay lanes on SCAN (2026-08-13): a line's hue says what carried
    # it. Distinct from the status colours on purpose — a green STATUS means
    # healthy, so the Wi-Fi lane is a leafier green; amber means warning, so
    # internet is a softer gold; violet is new to the vocabulary for BT.
    "link_wifi": "#9ccc65",
    "link_internet": "#ffd54f",
    "link_bt": "#b388ff",
    "text_primary": "#f0f0f0",
    "text_secondary": "#9e9e9e",
    "warning_yellow": "#ffd21e",   # requirement/hazard popups (dark text on this)
}

# Status thresholds (LiFePO4 deployment).
BATTERY_WARN_PCT = 20      # orange at or below
BATTERY_ALERT_PCT = 10     # red at or below
SIGNAL_WARN_DBM = -110     # orange at or below
SIGNAL_ALERT_DBM = -120    # red at or below
NOT_HEARD_ALERT_HOURS = 18  # red once not heard for this long (3x the 6 h beacon
                            # cadence — tolerate 2 missed announces before alerting)
QUIET_AFTER_HOURS = 12      # softer than the alert threshold: a node unheard this
                            # long sinks below the "quiet" divider in VITALS; any
                            # ping lifts it straight back up into the active list


# ---------------------------------------------------------------------------
# TYPE SCALE — the one place text size is decided.
#
# WHY THIS EXISTS (operator, 2026-08): "the font is too small for old eyes".
# The panel is a 5" 1280x720 LCD (~293 DPI) run at KIVY_METRICS_DENSITY=1.5
# (scripts/start_ui.sh), so the whole app lays out in 853x480 dp AND a nominal
# "15sp" body line lands on the glass about 5.5 pt tall. That is legible for
# young eyes and marginal for presbyopic ones, read standing up outdoors.
#
# The constraint that shapes the fix, in the operator's words: "font to be
# enlarged but make sure nothing gets cut off or squashed or forces a screen to
# scroll." So this is a COMPRESSIVE scale, not a flat multiplier:
#
#   * small text is pulled up hard (10-13sp grows 1.31-1.50x) — it is both the
#     unreadable text and the cheap text: a 13->17sp line costs ~6 dp of row
#     height, and most rows already carry that much slack.
#   * display text is left alone (>=25sp is identity) — it is already legible,
#     and every dp added to a 27sp hero line is a dp taken from a screen that
#     must not start scrolling.
#
# The ceiling on body text is set by the commonest row in the codebase,
# ``height=dp(24)``: a line of Ns needs about 1.32*N dp (measured off Kivy's
# bundled Roboto), so 18sp is the largest size that still fits dp(24) unchanged.
# 15sp — the single most-used size, 75 call sites — maps to exactly 18sp for
# that reason. Nothing lands below 15sp.
#
# Call sites keep their DESIGN size ("13sp" still means "small caption") and
# pass it through font_sp(); to make the whole UI a notch bigger later, move the
# control points below and nothing else.
#
# NOTE the honest limit: this buys roughly +20-30% on body text, not the ~1.7x
# that would make 5.5 pt comfortable. The lever for the rest is
# KIVY_METRICS_DENSITY, which scales sp AND dp together (so nothing can clip) at
# the cost of fitting less on each screen — a separate, deliberate decision.

#: Piecewise-linear (design sp -> shipped sp) control points, ascending.
#: Above the last point the scale is identity: display type is already big.
_TYPE_SCALE_POINTS = (
    (10.0, 15.0),   # x1.50 — the app's smallest text (a map scale caption)
    (13.0, 17.0),   # x1.31 — certificate / node-detail body, the named complaint
    (15.0, 18.0),   # x1.20 — the default body size; 18sp still fits a dp(24) row
    (18.0, 20.0),   # x1.11
    (22.0, 23.0),   # x1.05 — section titles
    (25.0, 25.0),   # x1.00 — identity from here up
)

#: Nothing may ship smaller than this. The operator's floor was "~13sp"; the
#: scale clears it because sub-13sp text is exactly what could not be read.
FONT_MIN_SP = 15.0

#: One line of Nsp text occupies about this many dp. Kivy gives a single-line
#: Label a texture height of the font's (ascent - descent); measured on the
#: bundled Roboto (regular AND bold — identical metrics) that ratio peaks at
#: 1.308, so 1.32 with a ceil() is an upper bound at every size — it can round a
#: row UP by a dp or two, never leave one too short to hold its text.
LINE_HEIGHT_RATIO = 1.32

#: RobotoMono — the face the version / hash / fingerprint rows use — is a taller
#: design: measured, it peaks at 1.419 over the sizes this scale emits. A row of
#: mono text sized with the Roboto ratio comes out 2-3 dp short and clips its
#: descenders, so ``line_dp(..., mono=True)`` exists and every mono row must use
#: it.
MONO_LINE_HEIGHT_RATIO = 1.43

#: Layout budget of the medic's panel in dp: 1280x720 physical / density 1.5.
#: A screen with no ScrollView has to fit its fixed heights inside PANEL_H_DP.
PANEL_W_DP = 1280 / 1.5
PANEL_H_DP = 720 / 1.5


def _sp_value(size) -> float:
    """Accept ``"13sp"``, ``"13"``, ``13`` or ``13.0`` and give back the number."""
    if isinstance(size, (int, float)):
        return float(size)
    text = str(size).strip()
    if text.endswith("sp"):
        text = text[:-2]
    return float(text)


def type_scale(size) -> float:
    """Map a DESIGN size to the size actually shipped, as a number.

    Monotonic and never shrinking: ``type_scale(x) >= x`` for every x, so no
    existing text can come out smaller than it is today.
    """
    v = _sp_value(size)
    pts = _TYPE_SCALE_POINTS
    if v <= pts[0][0]:
        out = pts[0][1]
    elif v >= pts[-1][0]:
        out = v                       # identity above the last control point
    else:
        out = v
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if x0 <= v <= x1:
                out = y0 + (v - x0) * (y1 - y0) / (x1 - x0)
                break
    out = round(out * 2.0) / 2.0      # land on half-points, not 16.333333sp
    return max(FONT_MIN_SP, out, v)


def font_sp(size) -> str:
    """A DESIGN size as the ``"Nsp"`` string Kivy wants. ``font_sp("13sp")`` ->
    ``"17sp"``. This is the call every widget should make."""
    v = type_scale(size)
    return f"{v:g}sp"


def line_dp(size, mono: bool = False) -> float:
    """dp height ONE line of text at DESIGN *size* needs once scaled.

    Pass ``mono=True`` for a RobotoMono row (see MONO_LINE_HEIGHT_RATIO).

    Pair it with a row's existing fixed height as ``max(h, line_dp(size))`` —
    that grows the container with the text and can never shrink it. The 2026-08
    lesson behind this: enlarging text inside a fixed-height container without
    enlarging the container in the SAME edit is how the earlier card-sizing
    attempt clipped its content and had to be reverted on the device.
    """
    ratio = MONO_LINE_HEIGHT_RATIO if mono else LINE_HEIGHT_RATIO
    return float(math.ceil(type_scale(size) * ratio))


def hex_to_rgba(value: str, alpha: float = 1.0) -> Tuple[float, float, float, float]:
    """Convert ``#rrggbb`` to a Kivy ``(r, g, b, a)`` tuple of 0-1 floats."""
    value = value.lstrip("#")
    r = int(value[0:2], 16) / 255.0
    g = int(value[2:4], 16) / 255.0
    b = int(value[4:6], 16) / 255.0
    return (r, g, b, alpha)


def battery_status(pct: float) -> str:
    if pct <= BATTERY_ALERT_PCT:
        return "alert"
    if pct <= BATTERY_WARN_PCT:
        return "warn"
    return "ok"


def signal_status(dbm: float) -> str:
    if dbm <= SIGNAL_ALERT_DBM:
        return "alert"
    if dbm <= SIGNAL_WARN_DBM:
        return "warn"
    return "ok"


def last_seen_status(hours: float) -> str:
    return "alert" if hours > NOT_HEARD_ALERT_HOURS else "ok"


_STATUS_TO_COLOR = {
    "ok": "green",
    "warn": "amber",
    "alert": "red",
}


def status_color(status: str) -> str:
    """Map a status word to a palette hex string. Unknown -> grey."""
    return COLORS.get(_STATUS_TO_COLOR.get(status, "text_secondary"))


def status_rgba(status: str, alpha: float = 1.0) -> Tuple[float, float, float, float]:
    return hex_to_rgba(status_color(status), alpha)
