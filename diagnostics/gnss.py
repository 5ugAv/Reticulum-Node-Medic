"""GNSS (GPS) diagnostics for the Heltec Wireless Tracker RNode.

The Tracker's firmware pushes its GPS fix over KISS; the serial splitter skims it
to a small JSON state file (``~/gps_state.json``). These checks read that file to
confirm the GNSS is powered, wired, and — outdoors — actually getting a fix. All
plain-English. They no-op on any node that isn't a Wireless Tracker, so adding the
module to the run is safe for every other kind of node.
"""

from __future__ import annotations

import json
from typing import List, Optional

from node_profile import NodeHardware
from diagnostics.base import DiagnosticCheck, Issue

GPS_STATE_PATH = "~/gps_state.json"
STALE_AFTER_S = 30
MIN_SATS = 4


class GnssCheck(DiagnosticCheck):
    category_name = "GPS (GNSS)"

    def _gps_state(self) -> Optional[dict]:
        out = self._cmd_output(f"cat {GPS_STATE_PATH}")
        try:
            return json.loads(out) if out.strip() else None
        except ValueError:
            return None

    def _node_epoch(self) -> Optional[int]:
        out = self._cmd_output("date +%s")
        try:
            return int(out.strip())
        except (ValueError, AttributeError):
            return None

    def run(self) -> List[Issue]:
        if self.profile.hardware is not NodeHardware.WIRELESS_TRACKER:
            return []                       # GNSS checks only apply to the Tracker

        st = self._gps_state()
        now = self._node_epoch()
        flowing = False
        if st is not None and now is not None:
            upd = st.get("updated")
            flowing = isinstance(upd, (int, float)) and (now - upd) <= STALE_AFTER_S

        issues: List[Optional[Issue]] = []
        issues.append(self._check(
            "gnss_data_flowing", flowing,
            "The Tracker isn't reporting any GPS data. Check it's plugged in, the "
            "GPS antenna is on the GNSS socket, and the GPS service is running — "
            "the GNSS shares its GPIO3 power with the display. No location is "
            "available right now.",
            severity="warning",
            raw_detail=f"state={st}, node_epoch={now}"))

        if flowing:
            sats = int(st.get("sats") or 0)
            has_fix = bool(st.get("has_fix"))
            issues.append(self._check(
                "gnss_has_fix", has_fix,
                "The GPS is powered and reporting, but hasn't found a location "
                f"yet ({sats} satellites so far). Give it a clear view of the sky "
                "— the first outdoor fix can take a few minutes.",
                severity="info",
                raw_detail=f"sats={sats}, fix={st.get('fix')}"))
            if has_fix:
                issues.append(self._check(
                    "gnss_enough_satellites", sats >= MIN_SATS,
                    f"The GPS has a location but only {sats} satellites, so the "
                    "position may be rough. More open sky, or a better-placed "
                    "antenna, will sharpen it.",
                    severity="info",
                    raw_detail=f"sats={sats}"))

        return [i for i in issues if i is not None]
