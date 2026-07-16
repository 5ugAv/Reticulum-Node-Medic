"""Download a basemap for offline use, when the medic has internet.

A field medic is usually offline, so the map is only useful if tiles were cached
while it last had WiFi. This fetches every tile within a radius of a point (the
download point — where the medic is, or an entered coordinate) across a zoom
range and writes them into an ``.mbtiles`` SQLite file in ``assets/maps/``, which
``ui.map_tiles.find_mbtiles`` then picks up automatically — no further setup.

The network fetch is injected (``fetch``) so the tile maths, circle clipping and
MBTiles writing are all unit-tested without touching the network. The default
fetcher pulls from Carto's CDN (OSM-based, attribution required — shown on the
SCAN screen). tile.openstreetmap.org must NOT be bulk-fetched: its policy
forbids it and its servers answer with "Access blocked" notice tiles (we
learned live). A same-bytes circuit breaker aborts rather than cache those.
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
#: A regional overview (z8) down to suburb level (z12) — a sensible default for
#: a node-coverage map. Higher max zoom multiplies the tile count ~4x per level;
#: bulk-caching street zooms is also what gets a client blocked by providers.
DEFAULT_MIN_ZOOM = 8
DEFAULT_MAX_ZOOM = 12
DEFAULT_RADIUS_KM = 100.0
#: Rough average PNG tile size, for a pre-download size estimate.
_AVG_TILE_KB = 15.0

#: Default basemap: Carto's CDN raster tiles (OSM data, no API key). We learned
#: the hard way that bulk-fetching tile.openstreetmap.org violates its usage
#: policy — the volunteer servers auto-block and serve "Access blocked" notice
#: tiles, poisoning the cache. Carto's CDN is built for app traffic; both
#: require the attribution shown on the SCAN screen.
OSM_URL = "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
ATTRIBUTION = "(c) OpenStreetMap contributors, (c) CARTO"
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


# ---- storage safety + download controls -------------------------------------

#: Maps may use at most this fraction of the *currently free* disk — the medic
#: must always keep room for its registry, logs and updates.
MAPS_BUDGET_FRACTION = 0.5
#: Selectable download radii (km) for the stepper control.
RADIUS_STEPS = [25.0, 50.0, 100.0, 150.0, 200.0]


def _fmt_size(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def disk_free_mb(path: str = ".") -> float:
    import shutil
    return shutil.disk_usage(path).free / (1024 * 1024)


def storage_summary(est_mb: float, free_mb: float,
                    budget_fraction: float = MAPS_BUDGET_FRACTION) -> Dict:
    """Plain-English size-vs-space verdict for the download control:
    ``{ok, text}``. The budget keeps maps from ever crowding the tool's own
    storage."""
    budget_mb = free_mb * budget_fraction
    if est_mb <= budget_mb:
        return {"ok": True, "text":
                f"Uses about {_fmt_size(est_mb)} of your "
                f"{_fmt_size(free_mb)} free space."}
    return {"ok": False, "text":
            f"Too big: about {_fmt_size(est_mb)}, but only "
            f"{_fmt_size(budget_mb)} is safely available for maps - "
            "reduce the radius."}


def ip_geolocate(fetch: Optional[Callable[[str], str]] = None,
                 timeout: float = 4.0) -> Optional[Tuple[float, float, str]]:
    """The medic's approximate position from its internet connection —
    city-level accuracy, which is plenty to centre a maps download (the only
    time this is used, and the only time the medic is guaranteed online).
    Returns (lat, lon, place) or None. ``fetch`` is injected for tests."""
    if fetch is None:
        def fetch(url: str) -> str:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
    import json as _json
    # two independent no-key services; first answer wins
    try:
        data = _json.loads(fetch("https://ipinfo.io/json"))
        loc = parse_latlon(data.get("loc", ""))
        if loc:
            return (loc[0], loc[1], data.get("city") or "your internet location")
    except Exception:
        pass
    try:
        data = _json.loads(fetch("http://ip-api.com/json/"))
        lat, lon = data.get("lat"), data.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return (float(lat), float(lon),
                    data.get("city") or "your internet location")
    except Exception:
        pass
    return None


def parse_latlon(text: str) -> Optional[Tuple[float, float]]:
    """Parse a typed "lat, lon" pair (home-base entry when there's no GPS fix).
    Returns None unless it's a plausible coordinate."""
    try:
        parts = text.replace(";", ",").split(",")
        if len(parts) != 2:
            return None
        lat, lon = float(parts[0].strip()), float(parts[1].strip())
    except (ValueError, AttributeError):
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180 and (lat, lon) != (0.0, 0.0):
        return (lat, lon)
    return None


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
    summary ``{total, fetched, skipped, failed, done, cancelled, blocked}``.
    """
    fetch = fetch or osm_fetch
    tiles = tiles_in_radius(lat, lon, radius_km, zmin, zmax)
    bounds = radius_bounds(lat, lon, radius_km)
    writer = MBTilesWriter(
        dest_path, name or f"offline {radius_km:g}km @ {lat:.3f},{lon:.3f}",
        bounds, zmin, zmax, center=f"{lon},{lat},{zmin}")
    total = len(tiles)
    fetched = skipped = failed = done = 0
    cancelled = blocked = False
    # Circuit breaker: providers that refuse a bulk client serve the SAME
    # "access blocked" notice image for every tile (with HTTP 200, so a status
    # check can't catch it). Distinct map tiles are never identical, so many
    # consecutive byte-identical bodies = we're blocked. Stop, don't poison.
    import hashlib
    _same_hash, _same_run = None, 0
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
                    h = hashlib.sha1(data).hexdigest()
                    if h == _same_hash:
                        _same_run += 1
                        # ocean tiles are legitimately byte-identical but tiny
                        # (solid-colour PNGs); a long run of LARGE identical
                        # tiles is a rendered "access blocked" notice.
                        if _same_run >= 24 and len(data) > 4096:
                            blocked = True
                            break
                    else:
                        _same_hash, _same_run = h, 1
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
    summary = {"total": total, "done": done, "fetched": fetched, "blocked": blocked,
               "skipped": skipped, "failed": failed, "cancelled": cancelled}
    if on_progress:
        on_progress(summary)
    return summary
