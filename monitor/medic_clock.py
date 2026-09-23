"""Is the medic's own clock worth signing? (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh" — the disciplined-clock rule, 2026-09-23.)

The medic feeds the time to Pi nodes over LoRa, signed. A signature makes
the node BELIEVE the value; it does nothing to make the value TRUE. The
Pi 5 medic has no battery-backed RTC and boots to a bogus time after a
power loss — exactly the failure it is curing on the node — so a medic
that signs whatever its clock says would propagate its own wrong clock to
every node it can reach, with a signature that tells them not to argue.

So the medic signs only a DISCIPLINED clock: one that GPS set recently
(monitor.gps_clock.apply_clock stamps last_disciplined_at) or that the OS
reports NTP-synchronised. Pure: every input is passed in.
"""
from __future__ import annotations

from typing import Optional, Tuple

#: A GPS discipline older than this no longer vouches for the clock: the
#: Pi 5's RTC drifts, and a medic that lost its fix six hours ago may have
#: been power-cycled since (which is what resets its clock). Six hours is
#: the nodes' own heartbeat, so a fresh reading is never far away.
MAX_GPS_AGE_S = 6 * 3600


def disciplined(now: float, gps_last_disciplined_at: Optional[float],
                ntp_synced: Optional[bool], max_gps_age_s: float = MAX_GPS_AGE_S
                ) -> Tuple[bool, str]:
    """(ok, reason). ok only when GPS set the clock within *max_gps_age_s*
    or NTP reports synchronised; the reason names the evidence either way,
    so the log line that refuses to sign says exactly what was missing."""
    try:
        now_f = float(now)
    except (TypeError, ValueError):
        return (False, "the medic's own clock could not be read")
    gps_age = None
    if gps_last_disciplined_at is not None:
        try:
            gps_age = now_f - float(gps_last_disciplined_at)
        except (TypeError, ValueError):
            gps_age = None
    if gps_age is not None and 0 <= gps_age <= float(max_gps_age_s):
        return (True, "GPS set this clock %s ago" % _age(gps_age))
    if ntp_synced is True:
        return (True, "NTP reports the clock synchronised")
    if gps_age is not None and gps_age > float(max_gps_age_s):
        return (False, "the last GPS discipline was %s ago (older than %s) and NTP "
                       "is not synchronised" % (_age(gps_age), _age(max_gps_age_s)))
    if gps_age is not None and gps_age < 0:
        return (False, "the last GPS discipline is stamped in the future (the clock "
                       "moved backwards since) and NTP is not synchronised")
    if ntp_synced is None:
        return (False, "no GPS discipline on record and the NTP state could not be read")
    return (False, "no GPS discipline on record and NTP is not synchronised")


def _age(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 90:
        return "%d s" % int(round(s))
    if s < 5400:
        return "%d min" % int(round(s / 60.0))
    return "%.1f h" % (s / 3600.0)
