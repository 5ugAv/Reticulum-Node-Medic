"""Offline map tiles for SCAN mode — Web Mercator math + an MBTiles reader.

A real basemap under the node dots, fully offline: tiles come from an **MBTiles**
file (one SQLite DB holding every z/x/y PNG) carried on the Pi, so there is no
internet dependency in the field. Everything here is pure and testable except the
SQLite read (testable against a temp DB).

Standard slippy-map / Web Mercator (EPSG:3857): tiles are 256 px, the world is
``2**zoom`` tiles across, tile pixel origin is TOP-left with y increasing DOWN.
Kivy's screen origin is BOTTOM-left (y UP), so screen conversions flip y. Node
dots MUST use the same projection + viewport transform as the tiles or they won't
line up — that's what MercatorView guarantees.
"""

from __future__ import annotations

import glob
import math
import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

TILE_SIZE = 256

#: Where carried offline basemaps live (gitignored, like firmware/packages).
MAPS_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "assets", "maps")


def project_px(lat: float, lon: float, zoom: int) -> Tuple[float, float]:
    """Web Mercator world-pixel coords at *zoom* (origin top-left, y DOWN).
    Latitude is clamped to the Mercator limit (~±85.05°)."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = (2 ** zoom) * TILE_SIZE
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def unproject_px(x: float, y: float, zoom: int) -> Tuple[float, float]:
    """Inverse of project_px: world pixels (y down) -> (lat, lon)."""
    n = (2 ** zoom) * TILE_SIZE
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n))))
    return lat, lon


def clamp_latlon(bounds, lat: float, lon: float) -> Tuple[float, float]:
    """Clamp (lat, lon) into a basemap *bounds* tuple (min_lon, min_lat, max_lon,
    max_lat), so a pan can't leave the cached area and strand the view in an
    all-black void. Returns the point unchanged when *bounds* is falsy."""
    if not bounds:
        return (lat, lon)
    w, s, e, n = bounds
    return (max(s, min(n, lat)), max(w, min(e, lon)))


def subtile_cell(x: int, y: int, k: int):
    """For tile (x,y), its ancestor *k* zoom levels up is (x>>k, y>>k); this tile
    is cell (col, row) in that ancestor's 2^k x 2^k grid (row 0 = top). Returns
    (col, row, cells) — used to overzoom a low-zoom tile into a missing high one."""
    cells = 1 << k
    return (x - ((x >> k) << k), y - ((y >> k) << k), cells)


def touch_separation(points) -> float:
    """Largest gap between any two touch points; 0.0 with fewer than two. Used to
    tell a real two-finger pinch (points far apart) from a panel reporting ONE
    finger as two nearby contact points (which must scroll, not zoom)."""
    if len(points) < 2:
        return 0.0
    return max(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
               for i, a in enumerate(points) for b in points[i + 1:])


def snap_zoom(zooms, z: int) -> int:
    """Snap *z* to the largest cached zoom <= z (or the smallest cached level),
    so the map only ever renders a zoom that actually has tiles — never a blank
    pane. Returns *z* unchanged when *zooms* is empty."""
    if not zooms:
        return z
    below = [c for c in zooms if c <= z]
    return below[-1] if below else zooms[0]


def step_zoom(zooms, current: int, direction: int) -> int:
    """The next cached zoom from *current* in *direction* (+1 zoom in / -1 out),
    skipping gaps (a missing mid-range level). Stays put at the cached edge."""
    if not zooms:
        return current + (1 if direction > 0 else -1)
    if direction > 0:
        higher = [c for c in zooms if c > current]
        return higher[0] if higher else current
    lower = [c for c in zooms if c < current]
    return lower[-1] if lower else current


def view_at(lat: float, lon: float, zoom: int,
            view_w: float, view_h: float) -> "MercatorView":
    """A viewport centred on (lat, lon) at an explicit zoom — the interactive
    (pan/pinch) counterpart of build_view's fit-a-bbox."""
    cx, cy = project_px(lat, lon, zoom)
    return MercatorView(zoom=zoom, off_x=cx - view_w / 2.0,
                        off_y=cy - view_h / 2.0, width=view_w, height=view_h)


def tile_of(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
    """The (x, y) tile index containing (lat, lon) at *zoom*."""
    px, py = project_px(lat, lon, zoom)
    return int(px // TILE_SIZE), int(py // TILE_SIZE)


def fit_zoom(min_lat: float, max_lat: float, min_lon: float, max_lon: float,
             view_w: float, view_h: float,
             min_zoom: int = 1, max_zoom: int = 19) -> int:
    """The largest zoom at which the whole bbox fits inside *view_w* x *view_h*.
    A zero-extent bbox (single point) returns *max_zoom*."""
    for z in range(max_zoom, min_zoom - 1, -1):
        x0, y_top = project_px(max_lat, min_lon, z)   # north-west
        x1, y_bot = project_px(min_lat, max_lon, z)   # south-east
        if abs(x1 - x0) <= view_w and abs(y_bot - y_top) <= view_h:
            return z
    return min_zoom


@dataclass
class MercatorView:
    """A fitted viewport: the zoom and the world-pixel offset that centres the
    bbox. Subtract the offset from world pixels to get screen pixels."""
    zoom: int
    off_x: float
    off_y: float          # world-px (y down)
    width: float
    height: float

    def to_screen(self, lat: float, lon: float) -> Tuple[float, float]:
        """Geo -> Kivy screen coords (origin bottom-left, y up)."""
        x, y = project_px(lat, lon, self.zoom)
        return x - self.off_x, self.height - (y - self.off_y)

    def to_latlon(self, sx: float, sy: float) -> Tuple[float, float]:
        """Kivy screen coords (origin bottom-left, y up) -> geo. Inverse of
        to_screen — used for tap-to-place a pin on the offline map."""
        wx = sx + self.off_x
        wy = self.off_y + (self.height - sy)
        return unproject_px(wx, wy, self.zoom)


def build_view(min_lat: float, max_lat: float, min_lon: float, max_lon: float,
               view_w: float, view_h: float, padding: float = 0.0,
               max_zoom: int = 19) -> MercatorView:
    """Fit the bbox into the viewport and centre it. Padding shrinks the fit area
    so dots near the edge aren't clipped."""
    zoom = fit_zoom(min_lat, max_lat, min_lon, max_lon,
                    max(view_w - 2 * padding, 1.0),
                    max(view_h - 2 * padding, 1.0), max_zoom=max_zoom)
    x0, y_top = project_px(max_lat, min_lon, zoom)
    x1, y_bot = project_px(min_lat, max_lon, zoom)
    cx = (x0 + x1) / 2.0
    cy = (y_top + y_bot) / 2.0
    return MercatorView(zoom=zoom, off_x=cx - view_w / 2.0,
                        off_y=cy - view_h / 2.0, width=view_w, height=view_h)


@dataclass
class TilePlacement:
    z: int
    x: int
    y: int
    screen_x: float       # Kivy bottom-left of the tile
    screen_y: float


def tiles_for_view(view: MercatorView) -> List[TilePlacement]:
    """Every tile needed to cover *view*, with its Kivy screen position (the
    tile's bottom-left corner). Out-of-world tiles are dropped."""
    z = view.zoom
    n = 2 ** z
    wx0, wx1 = view.off_x, view.off_x + view.width
    wy0, wy1 = view.off_y, view.off_y + view.height   # world-px, y down
    out: List[TilePlacement] = []
    for tx in range(int(wx0 // TILE_SIZE), int(wx1 // TILE_SIZE) + 1):
        for ty in range(int(wy0 // TILE_SIZE), int(wy1 // TILE_SIZE) + 1):
            if not (0 <= tx < n and 0 <= ty < n):
                continue
            world_x = tx * TILE_SIZE
            world_y = ty * TILE_SIZE
            sx = world_x - view.off_x
            # Kivy pos is the bottom-left; the tile spans TILE_SIZE downward in
            # world-px, so its bottom edge is world_y + TILE_SIZE.
            sy = view.height - (world_y + TILE_SIZE - view.off_y)
            out.append(TilePlacement(z, tx, ty, sx, sy))
    return out


class MBTiles:
    """Read-only MBTiles (SQLite) tile source. MBTiles stores rows in TMS order
    (y flipped vs the XYZ/slippy scheme this module uses), so get_tile flips."""

    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)

    def get_tile(self, z: int, x: int, y: int) -> Optional[bytes]:
        tms_y = (2 ** z - 1) - y
        row = self.conn.execute(
            "SELECT tile_data FROM tiles "
            "WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y)).fetchone()
        return row[0] if row else None

    def zoom_levels(self) -> list:
        """Sorted list of zoom levels that actually have tiles. The cache is
        often sparse (e.g. street zooms not downloaded, or a gap mid-range), so
        the map must only ever request a zoom that EXISTS here — otherwise
        get_tile returns None and the pane goes blank."""
        rows = self.conn.execute(
            "SELECT DISTINCT zoom_level FROM tiles ORDER BY zoom_level").fetchall()
        return [r[0] for r in rows]

    def bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """(min_lon, min_lat, max_lon, max_lat) from metadata, or None."""
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE name='bounds'").fetchone()
        if not row:
            return None
        try:
            w, s, e, n = (float(v) for v in row[0].split(","))
            return (w, s, e, n)
        except (ValueError, TypeError):
            return None

    def close(self):
        self.conn.close()


def find_mbtiles(maps_dir: str = MAPS_DIR) -> Optional["MBTiles"]:
    """Open the first carried .mbtiles basemap in *maps_dir*, or None if none is
    present (the Map screen then falls back to the coord plot)."""
    hits = sorted(glob.glob(os.path.join(maps_dir, "*.mbtiles")))
    return MBTiles(hits[0]) if hits else None
