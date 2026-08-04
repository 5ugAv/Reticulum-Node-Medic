"""Terrain between two points — does the radio actually have a path?

WHY THIS EXISTS. ``monitor.placement`` estimates signal from DISTANCE alone
(``estimate_rssi_dbm``: log-distance path loss, exponent 2.7). Distance is the
wrong question. FAITH at Sampleton to the medic at Stonefield was under a kilometre — a
distance the model calls comfortable — and the link failed, because the path ran
through houses with no line of sight ([[first-range-test-faith]]). A suggester
that cannot see a hill or a building will keep recommending positions that fail
for reasons it never modelled.

WHAT THIS IS NOT. It is not a propagation model. Longley-Rice/ITWOM is the real
thing and MeshCommunityPlanner does it properly, but that project is
CC BY-NC-SA — its licence would follow into anything that borrowed its code. So
this uses only the underlying PUBLIC-DOMAIN data (NASA SRTM) and first-principles
geometry, and answers a narrower, honest question: **is the straight line
between these two points blocked by ground?**

Terrain is not buildings. SRTM is a surface model of bare earth at 30 m
postings; a clear terrain profile through a suburb is still a suburb. So a PASS
here means "the ground does not block it", never "this link will work". A FAIL
is the stronger signal, and that asymmetry is deliberate — see ``verdict``.

Offline by construction: reads local .hgt tiles, never the network. Missing tile
means "unknown", never "fine".
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

#: NASA SRTM 1-arc-second: a 1°x1° tile as 3601x3601 big-endian int16, ~26 MB.
#: 3-arc-second (SRTM3) tiles are 1201x1201; both are supported by inferring the
#: grid from the file size, because mixing them silently would misplace every
#: sample by up to 90 m.
SRTM_VOID = -32768                 # SRTM's own "no data" marker

#: Speed of light, for the Fresnel radius.
_C = 299_792_458.0

#: Radio horizon uses an effective earth radius 4/3 of the true one — the
#: standard allowance for atmospheric refraction bending signals slightly around
#: the curve. Using the true radius would over-report blockage on long paths.
EARTH_R_M = 6_371_000.0
K_FACTOR = 4.0 / 3.0

#: A path is treated as obstructed when less than this fraction of the first
#: Fresnel zone is clear. 0.6 is the long-standing engineering rule of thumb:
#: below ~60% clearance, diffraction loss climbs steeply.
FRESNEL_CLEARANCE = 0.6


def tile_name(lat: float, lon: float) -> str:
    """The .hgt filename covering a point, e.g. S38E145.hgt for Sampleton.

    Tiles are named for their SOUTH-WEST corner, so the latitude floor is taken
    before the hemisphere letter is applied — S38 covers -38.0 to -37.0.
    """
    la = math.floor(lat)
    lo = math.floor(lon)
    ns = "N" if la >= 0 else "S"
    ew = "E" if lo >= 0 else "W"
    return f"{ns}{abs(la):02d}{ew}{abs(lo):03d}.hgt"


def grid_size_for(nbytes: int) -> Optional[int]:
    """Samples per side implied by a tile's size, or None if it isn't one."""
    for n in (3601, 1201):                     # 1-arc-sec, 3-arc-sec
        if nbytes == n * n * 2:
            return n
    return None


class TileStore:
    """Local .hgt tiles. No network, ever — this runs in the field."""

    def __init__(self, directory: str):
        self.directory = directory
        self._cache: dict = {}

    def _load(self, name: str):
        if name in self._cache:
            return self._cache[name]
        path = os.path.join(self.directory, name)
        data = None
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
            n = grid_size_for(len(raw))
            if n:
                data = (n, raw)
        except Exception:
            data = None
        self._cache[name] = data
        return data

    def elevation(self, lat: float, lon: float) -> Optional[float]:
        """Ground height in metres, or None when unknown.

        None means "no tile" or "SRTM void" — NEVER zero. Returning 0 for a
        missing tile would silently place every unknown point at sea level and
        make a mountain look like clear air.
        """
        tile = self._load(tile_name(lat, lon))
        if not tile:
            return None
        n, raw = tile
        # position within the tile, from its south-west corner
        fy = (math.floor(lat) + 1) - lat        # rows run north -> south
        fx = lon - math.floor(lon)
        row = min(n - 1, max(0, int(fy * (n - 1))))
        col = min(n - 1, max(0, int(fx * (n - 1))))
        off = (row * n + col) * 2
        try:
            (v,) = struct.unpack_from(">h", raw, off)
        except Exception:
            return None
        return None if v == SRTM_VOID else float(v)


class TerrariumStore:
    """Elevation from the tiles the map download already fetched.

    Same z/x/y scheme as the basemap, so one button caches both. Elevation is
    packed into the pixel: ``metres = (R * 256 + G + B / 256) - 32768``.

    Reads the .mbtiles cache and nothing else. Missing tile means None, never
    zero — the whole point of the TileStore contract, for the same reason: a
    missing tile that read as sea level would make a mountain look like clear
    air.
    """

    def __init__(self, mbtiles_path: str, zoom: int = 12):
        self.path = mbtiles_path
        self.zoom = zoom
        self._cache: dict = {}

    @staticmethod
    def decode(r: int, g: int, b: int) -> float:
        """Terrarium pixel -> metres above sea level."""
        return (r * 256 + g + b / 256.0) - 32768.0

    def _tile(self, x: int, y: int):
        key = (self.zoom, x, y)
        if key in self._cache:
            return self._cache[key]
        img = None
        try:
            import sqlite3
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
            # MBTiles stores rows flipped (TMS); the map writer already does
            # this conversion on the way in, so mirror it on the way out.
            flipped = (2 ** self.zoom - 1) - y
            row = con.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND "
                "tile_column=? AND tile_row=?", (self.zoom, x, flipped)).fetchone()
            con.close()
            if row:
                import io
                from PIL import Image
                img = Image.open(io.BytesIO(row[0])).convert("RGB")
        except Exception:
            img = None
        self._cache[key] = img
        return img

    def elevation(self, lat: float, lon: float) -> Optional[float]:
        z = self.zoom
        n = 2 ** z
        xf = (lon + 180.0) / 360.0 * n
        lat_r = math.radians(max(-85.05, min(85.05, lat)))
        yf = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r))
              / math.pi) / 2.0 * n
        img = self._tile(int(xf), int(yf))
        if img is None:
            return None
        w, h = img.size
        px = int((xf - int(xf)) * w)
        py = int((yf - int(yf)) * h)
        try:
            r, g, b = img.getpixel((min(w - 1, px), min(h - 1, py)))
        except Exception:
            return None
        m = self.decode(r, g, b)
        # terrarium encodes ocean as ~0; -32768 would be a decode failure
        return None if m < -12000 else m


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def fresnel_radius_m(freq_hz: float, d1_m: float, d2_m: float) -> float:
    """Radius of the first Fresnel zone at a point d1/d2 along the path."""
    total = d1_m + d2_m
    if total <= 0:
        return 0.0
    lam = _C / freq_hz
    return math.sqrt(max(0.0, lam * d1_m * d2_m / total))


def earth_bulge_m(d1_m: float, d2_m: float) -> float:
    """How far the earth's curve rises between the endpoints, at d1/d2."""
    return (d1_m * d2_m) / (2.0 * K_FACTOR * EARTH_R_M)


@dataclass
class PathVerdict:
    status: str                    # "clear" | "obstructed" | "unknown"
    reason: str
    worst_clearance: Optional[float] = None    # fraction of the Fresnel radius
    worst_at_km: Optional[float] = None
    samples: int = 0

    @property
    def is_blocked(self) -> bool:
        return self.status == "obstructed"


def line_of_sight(store: TileStore, lat1: float, lon1: float, h1_m: float,
                  lat2: float, lon2: float, h2_m: float,
                  freq_hz: float = 915_125_000.0,
                  step_m: float = 90.0) -> PathVerdict:
    """Walk the ground between two antennas and report the tightest squeeze.

    *h1_m* / *h2_m* are antenna heights ABOVE GROUND, not above sea level —
    that is what an operator can actually tell you ("it's on a 3 m pole").

    Returns "unknown" if any sample has no tile: a partial profile can hide the
    one hill that matters, and guessing would be worse than admitting it.
    """
    total = haversine_m(lat1, lon1, lat2, lon2)
    if total <= 0:
        return PathVerdict("clear", "Same point.", 1.0, 0.0, 0)
    e1 = store.elevation(lat1, lon1)
    e2 = store.elevation(lat2, lon2)
    if e1 is None or e2 is None:
        return PathVerdict("unknown", "No terrain data for one of the endpoints.")
    a1, a2 = e1 + h1_m, e2 + h2_m
    n = max(2, int(total / max(10.0, step_m)))
    worst, worst_at = None, None
    for i in range(1, n):
        f = i / n
        lat = lat1 + (lat2 - lat1) * f
        lon = lon1 + (lon2 - lon1) * f
        ground = store.elevation(lat, lon)
        if ground is None:
            return PathVerdict("unknown",
                               "Terrain data is missing partway along the path.",
                               samples=i)
        d1 = total * f
        d2 = total - d1
        sight = a1 + (a2 - a1) * f                 # the straight line
        obstacle = ground + earth_bulge_m(d1, d2)  # ground, plus the curve
        r = fresnel_radius_m(freq_hz, d1, d2)
        clearance = (sight - obstacle) / r if r > 0 else 0.0
        if worst is None or clearance < worst:
            worst, worst_at = clearance, d1 / 1000.0
    if worst is None:
        return PathVerdict("unknown", "Path too short to sample.")
    if worst >= FRESNEL_CLEARANCE:
        return PathVerdict("clear",
                           "Ground is clear of the path.",
                           worst, worst_at, n)
    if worst <= 0:
        return PathVerdict("obstructed",
                           f"Ground blocks the path {worst_at:.1f} km along.",
                           worst, worst_at, n)
    return PathVerdict("obstructed",
                       f"Only {worst * 100:.0f}% of the Fresnel zone is clear "
                       f"{worst_at:.1f} km along — expect heavy loss.",
                       worst, worst_at, n)


def advice(v: PathVerdict) -> str:
    """What the operator should DO about it, in their terms."""
    if v.status == "unknown":
        return ("No terrain map for this area, so this is distance only — "
                "walk the path or test before committing to it.")
    if v.status == "clear":
        return ("The ground is clear between them. Buildings and trees are NOT "
                "in this map, so line of sight still needs eyes on it.")
    if v.worst_clearance is not None and v.worst_clearance <= 0:
        return "Raise the antenna, or move the node to higher ground."
    return "Raising either antenna a few metres may be enough."
