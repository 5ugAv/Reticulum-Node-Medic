"""Whether the medic can offer its own satellite fix as a node's position —
pure, so the Location & map popup's second road is testable without Kivy.

Operator, 2026-09-23: after birth a node's position must be settable the
same three ways as at birth — type an address, move the pin, or take the
GPS fix "when the medic has satellite connection". The fix is offered only
when it is LIVE (monitor.geo.classify_fix: tracking satellites now); a held
or absent fix is refused with the reason, never silently taken.
"""
from typing import Optional

from monitor.geo import GpsFix, classify_fix, format_coord


def gps_offer(fix: Optional[GpsFix]) -> dict:
    """``{"ok": bool, "lat", "lon", "acc_m", "coords", "reason"}``.

    *coords* is the human line ("-37.7000, 145.0000"); *acc_m* is the HDOP
    estimate in metres or None (never invented); *reason* says why not."""
    state = classify_fix(fix)
    if state != "live" or fix is None:
        return {"ok": False, "lat": None, "lon": None, "acc_m": None, "coords": "",
                "reason": state}
    try:
        lat, lon = float(fix.lat), float(fix.lon)
    except (TypeError, ValueError):
        return {"ok": False, "lat": None, "lon": None, "acc_m": None, "coords": "",
                "reason": "invalid"}
    acc = fix.accuracy_m
    try:
        acc = None if acc is None else float(acc)
    except (TypeError, ValueError):
        acc = None
    return {"ok": True, "lat": lat, "lon": lon, "acc_m": acc,
            "coords": f"{format_coord(lat)}, {format_coord(lon)}", "reason": ""}
