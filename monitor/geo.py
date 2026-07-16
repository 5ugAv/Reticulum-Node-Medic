"""Location capture and navigation helpers.

At build time the Pi is physically at the node, so its GPS fix *is* the node's
location. We capture it once: the node advertises a privacy-fuzzed (~800 m,
firmware-side) pin to the public mesh map, while the exact coordinates are kept
on the birth certificate for a future repair visit.

The GPS read is injected so this is testable without hardware; the default
reader queries gpsd. The coordinate/URL helpers are pure.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple


@dataclass
class GpsFix:
    lat: float
    lon: float
    source: str = "pi_gps"
    sats: Optional[int] = None          # satellites used (from the Tracker STATE frame)
    fix_quality: Optional[int] = None   # 0 = no fix, >=1 = fix
    accuracy_m: Optional[float] = None  # None until the firmware reports HDOP (follow-up)
    fix_time: Optional[str] = None      # ISO-8601 UTC of the observation

    @property
    def has_fix(self) -> bool:
        return self.lat is not None and self.lon is not None


def _default_gps_reader() -> Optional[Tuple[float, float]]:
    """Query gpsd for a current fix; return (lat, lon) or None."""
    try:
        out = subprocess.run(
            ["gpspipe", "-w", "-n", "10"],
            capture_output=True, text=True, timeout=15).stdout
        for line in out.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("class") == "TPV" and "lat" in obj and "lon" in obj:
                return (obj["lat"], obj["lon"])
    except Exception:
        return None
    return None


def read_gps(reader: Callable[[], Optional[Tuple[float, float]]]
             = _default_gps_reader) -> Optional[GpsFix]:
    """Return a ``GpsFix`` or ``None`` if there's no fix / no GPS."""
    try:
        coords = reader()
    except Exception:
        return None
    if not coords:
        return None
    lat, lon = coords
    return GpsFix(lat=lat, lon=lon, source="pi_gps")


# --- Tracker GPS via the serial splitter -----------------------------------
# Jonesey (the medic's RNode) skims its own GPS fix into a small JSON state file
# (monitor.serial_splitter), so LoRa (rnsd) and GPS never fight over the one serial
# port. We read that file here rather than owning a port ourselves.

SPLITTER_STATE = os.path.expanduser("~/gps_state.json")


def read_splitter_state(path: str = SPLITTER_STATE, max_age_s: float = 30.0,
                        now: Callable[[], float] = time.time) -> Optional[dict]:
    """The splitter's latest GPS state, or ``None`` if the file is missing,
    unreadable, or older than *max_age_s* (the GPS/splitter isn't feeding now)."""
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    upd = st.get("updated")
    if not isinstance(upd, (int, float)) or (now() - upd) > max_age_s:
        return None
    return st


def read_splitter_fix(path: str = SPLITTER_STATE, max_age_s: float = 30.0,
                      now: Callable[[], float] = time.time) -> Optional[GpsFix]:
    """A full :class:`GpsFix` from the splitter state (position + sats + fix time),
    or ``None`` if there's no current fix. Used for the birth cert / Triage."""
    st = read_splitter_state(path, max_age_s, now)
    if not st or not st.get("has_fix"):
        return None
    fix_time = datetime.fromtimestamp(st["updated"], timezone.utc).isoformat()
    return GpsFix(lat=st["lat"], lon=st["lng"], source="tracker_gps",
                  sats=st.get("sats"), fix_quality=st.get("fix"), fix_time=fix_time)


def splitter_gps_reader(path: str = SPLITTER_STATE, max_age_s: float = 30.0
                        ) -> Callable[[], Optional[Tuple[float, float]]]:
    """A ``read_gps``-compatible reader (``() -> (lat, lon) | None``) sourced from
    the Tracker's GPS via the splitter. Drop into ``read_gps``, map centring, or the
    birth cert wherever a ``gps_reader`` is accepted."""
    def reader() -> Optional[Tuple[float, float]]:
        st = read_splitter_state(path, max_age_s)
        if st and st.get("has_fix"):
            return (st["lat"], st["lng"])
        return None
    return reader


# --- location privacy --------------------------------------------------------
# A node's EXACT coordinates belong to its builder only (birth cert, servicing,
# street-detail maps). What's ever shared publicly is a FUZZED pin: enough for
# "there's a working node in this area", useless for finding the hardware.

FUZZ_RADIUS_M = 800.0


def fuzz_location(lat: float, lon: float, node_key: str,
                  radius_m: float = FUZZ_RADIUS_M) -> Tuple[float, float, float]:
    """A privacy-fuzzed public position: (lat, lon, radius_m).

    The offset is DETERMINISTIC per node (seeded by *node_key*, e.g. the
    destination hash): the same fake position every time. This matters — a
    random offset per announce could be averaged away by an observer to
    recover the true location. It is also never centred on the real point
    (30-100% of the radius out), so the true position isn't at the middle of
    the advertised circle."""
    import hashlib
    import math
    digest = hashlib.sha256(f"{node_key}:location-fuzz".encode()).digest()
    angle = int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF * 2 * math.pi
    frac = 0.3 + int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF * 0.7
    dist = radius_m * frac
    dlat = (dist * math.cos(angle)) / 111_320.0
    dlon = (dist * math.sin(angle)) / (111_320.0 *
                                       max(0.05, math.cos(math.radians(lat))))
    return (lat + dlat, lon + dlon, radius_m)


def format_coord(deg: float) -> str:
    """Signed decimal degrees, 6 dp (~0.1 m resolution)."""
    return f"{deg:.6f}"


def maps_url(lat: float, lon: float, provider: str = "google") -> str:
    """A turn-by-turn directions deep link to the coordinates."""
    la, lo = format_coord(lat), format_coord(lon)
    if provider == "apple":
        return f"https://maps.apple.com/?daddr={la},{lo}"
    return f"https://www.google.com/maps/dir/?api=1&destination={la},{lo}"


def navigation_links(lat: float, lon: float) -> dict:
    return {
        "google": maps_url(lat, lon, "google"),
        "apple": maps_url(lat, lon, "apple"),
        "raw": f"{format_coord(lat)}, {format_coord(lon)}",
    }
