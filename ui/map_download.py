"""Download a basemap for offline use, when the medic has internet.

A field medic is usually offline, so the map is only useful if tiles were cached
while it last had WiFi. This fetches every tile within a radius of a point (the
download point — where the medic is, or an entered coordinate) across a zoom
range and writes them into an ``.mbtiles`` SQLite file in ``assets/maps/``, which
``ui.map_tiles.find_mbtiles`` then picks up automatically — no further setup.

The network fetch is injected (``fetch``) so the tile maths, circle clipping and
MBTiles writing are all unit-tested without touching the network. The default
fetcher pulls from OpenStreetMap; that server's tile-usage policy discourages
bulk downloads, so the URL template and User-Agent are overridable to point at a
provider you're entitled to bulk-cache from, and the download is rate-limited.
"""

from __future__ import annotations

import math
import sqlite3
import time
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from ui.map_tiles import tile_of

#: 1 degree of latitude in km (mean); longitude scales by cos(latitude).
_KM_PER_DEG = 111.32
_EARTH_R_KM = 6371.0088
#: A regional overview (z8) down to street level (z13) — a sensible default for a
#: node-coverage map. Higher max zoom multiplies the tile count ~4x per level.
DEFAULT_MIN_ZOOM = 8
DEFAULT_MAX_ZOOM = 13
DEFAULT_RADIUS_KM = 100.0
#: Rough average PNG tile size, for a pre-download size estimate.
_AVG_TILE_KB = 15.0

OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "ReticulumNodeMedic/1.0 (+offline field node-coverage map)"


def is_online(host: str = "1.1.1.1", port: int = 53, timeout: float = 3.0) -> bool:
    """Quick internet check before offering a download — a TCP connect to a
    public DNS resolver (no HTTP request, no tile-server load)."""
    import socket
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


# ---- geometry ------------------------------------------------------------

def _km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Equirectangular distance in km — plenty accurate at these radii."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return math.hypot(x, y) * _EARTH_R_KM


def _tile_center(x: int, y: int, z: int) -> Tuple[float, float]:
    """(lat, lon) of the centre of tile (x, y) at zoom z."""
    n = 2 ** z
    lon = (x + 0.5) / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 0.5) / n))))
    return lat, lon


def radius_bounds(lat: float, lon: float,
                  radius_km: float) -> Tuple[float, float, float, float]:
    """Bounding box (west, south, east, north) that encloses the radius circle."""
    dlat = radius_km / _KM_PER_DEG
    dlon = radius_km / (_KM_PER_DEG * max(0.01, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def tiles_in_radius(lat: float, lon: float, radius_km: float = DEFAULT_RADIUS_KM,
                    zmin: int = DEFAULT_MIN_ZOOM,
                    zmax: int = DEFAULT_MAX_ZOOM) -> List[Tuple[int, int, int]]:
    """Every (z, x, y) tile whose centre is within *radius_km* of (lat, lon),
    across the zoom range — clipped to the circle so a radius is a real circle,
    not its bounding square (saves ~20% of tiles)."""
    west, south, east, north = radius_bounds(lat, lon, radius_km)
    out: List[Tuple[int, int, int]] = []
    for z in range(zmin, zmax + 1):
        x0, y0 = tile_of(north, west, z)          # NW corner -> min x, min y
        x1, y1 = tile_of(south, east, z)          # SE corner -> max x, max y
        span = 2 ** z
        for x in range(max(0, x0), min(span - 1, x1) + 1):
            for y in range(max(0, y0), min(span - 1, y1) + 1):
                clat, clon = _tile_center(x, y, z)
                if _km_between(lat, lon, clat, clon) <= radius_km:
                    out.append((z, x, y))
    return out


def estimate_download(lat: float, lon: float, radius_km: float = DEFAULT_RADIUS_KM,
                      zmin: int = DEFAULT_MIN_ZOOM,
                      zmax: int = DEFAULT_MAX_ZOOM) -> Tuple[int, float]:
    """(tile count, approx MB) so the UI can warn before a large download."""
    n = len(tiles_in_radius(lat, lon, radius_km, zmin, zmax))
    return n, round(n * _AVG_TILE_KB / 1024.0, 1)


# ---- MBTiles writer ------------------------------------------------------

class MBTilesWriter:
    """Writes tiles into an MBTiles SQLite file that ``ui.map_tiles.MBTiles``
    reads back. Tiles are stored TMS-flipped (row = 2^z-1 - y) to match the
    reader. Idempotent: ``has`` lets a download resume without re-fetching."""

    def __init__(self, path: str, name: str,
                 bounds: Tuple[float, float, float, float],
                 minzoom: int, maxzoom: int, center: Optional[str] = None,
                 fmt: str = "png"):
        self.conn = sqlite3.connect(path)
        c = self.conn
        c.execute("CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, "
                  "tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS tile_index ON "
                  "tiles (zoom_level, tile_column, tile_row)")
        c.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS metadata_index ON "
                  "metadata (name)")
        w, s, e, n = bounds
        meta = {"name": name, "format": fmt, "type": "baselayer",
                "version": "1.1", "minzoom": str(minzoom),
                "maxzoom": str(maxzoom), "bounds": f"{w},{s},{e},{n}"}
        if center:
            meta["center"] = center
        for k, v in meta.items():
            c.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (k, v))
        c.commit()

    def has(self, z: int, x: int, y: int) -> bool:
        tms = (2 ** z - 1) - y
        return self.conn.execute(
            "SELECT 1 FROM tiles WHERE zoom_level=? AND tile_column=? AND "
            "tile_row=?", (z, x, tms)).fetchone() is not None

    def put(self, z: int, x: int, y: int, data: bytes) -> None:
        tms = (2 ** z - 1) - y
        self.conn.execute("INSERT OR REPLACE INTO tiles VALUES (?, ?, ?, ?)",
                          (z, x, tms, sqlite3.Binary(data)))

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()


# ---- network fetch (the injected seam) -----------------------------------

def osm_fetch(z: int, x: int, y: int, url_template: str = OSM_URL,
              user_agent: str = USER_AGENT, timeout: int = 15) -> Optional[bytes]:
    """Fetch one tile over HTTP. Returns the bytes, or None on any error so a
    single missing tile never aborts the whole download."""
    url = url_template.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status and resp.status >= 400:
                return None
            return resp.read()
    except Exception:
        return None


# ---- the download --------------------------------------------------------

def download_region(lat: float, lon: float, dest_path: str,
                    radius_km: float = DEFAULT_RADIUS_KM,
                    zmin: int = DEFAULT_MIN_ZOOM, zmax: int = DEFAULT_MAX_ZOOM,
                    name: Optional[str] = None,
                    fetch: Optional[Callable[[int, int, int], Optional[bytes]]] = None,
                    on_progress: Optional[Callable[[Dict], None]] = None,
                    rate_limit_s: float = 0.1,
                    stop: Optional[Callable[[], bool]] = None) -> Dict:
    """Cache every tile in the radius into *dest_path* (an .mbtiles file).

    Resumable (skips tiles already stored), cancellable (``stop()`` -> True),
    and rate-limited between network fetches to stay a polite client. Returns a
    summary ``{total, fetched, skipped, failed, done, cancelled}``.
    """
    fetch = fetch or osm_fetch
    tiles = tiles_in_radius(lat, lon, radius_km, zmin, zmax)
    bounds = radius_bounds(lat, lon, radius_km)
    writer = MBTilesWriter(
        dest_path, name or f"offline {radius_km:g}km @ {lat:.3f},{lon:.3f}",
        bounds, zmin, zmax, center=f"{lon},{lat},{zmin}")
    total = len(tiles)
    fetched = skipped = failed = done = 0
    cancelled = False
    try:
        for z, x, y in tiles:
            if stop and stop():
                cancelled = True
                break
            if writer.has(z, x, y):
                skipped += 1
            else:
                data = fetch(z, x, y)
                if data:
                    writer.put(z, x, y, data)
                    fetched += 1
                    if rate_limit_s:
                        time.sleep(rate_limit_s)
                else:
                    failed += 1
            done += 1
            if on_progress and done % 20 == 0:
                writer.commit()
                on_progress({"total": total, "done": done, "fetched": fetched,
                             "skipped": skipped, "failed": failed})
    finally:
        writer.close()
    summary = {"total": total, "done": done, "fetched": fetched,
               "skipped": skipped, "failed": failed, "cancelled": cancelled}
    if on_progress:
        on_progress(summary)
    return summary
