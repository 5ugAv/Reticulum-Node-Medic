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
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass
class GpsFix:
    lat: float
    lon: float
    source: str = "pi_gps"

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
