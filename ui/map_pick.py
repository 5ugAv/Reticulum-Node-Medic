"""Pure maths and words for CHOOSING a node's location on the offline map.

Built 2026-09-22 for the birth's location step (operator: "the user should be
able to zoom in and out of the map, and also select a location by pressing an
icon on the map and saying 'select here'"). Everything here is Kivy-free so it
can be tested directly; ``ui/widgets/confirm_location.py`` and ``MapPlot`` in
``ui/screens/scan_screen.py`` call in, they do not copy.

Two rules this module keeps in one place:

* **Zoom is clamped to what the cache has.** Below the deepest cached level
  the pane only renders levels that exist (no blank mid-range); above it the
  drawer ENLARGES the nearest cached ancestor ("blurry beats black") but only
  as far as the widget's overzoom cap. Nothing here reaches for the network —
  a birth happens in the field, offline.
* **What is printed is what is saved.** One float pair, one format, used for
  the coordinate line AND handed to the certificate.

This module chooses a location; it says nothing about how it is SHARED — the
privacy rules (exact coordinates stay local, a fuzzed pin is announced) live in
``monitor/geo.py`` and are untouched.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from ui.i18n import tr  # i18n: wrapped — the zoom/tiles caption
from ui.map_tiles import snap_zoom, step_zoom


def next_zoom(zooms: List[int], current: int, direction: int,
              overzoom_max: int, fallback_max: int) -> int:
    """The level a +/− press (or one pinch step) lands on from *current*.

    Steps between CACHED levels, skipping gaps; past the deepest cached
    level, one level at a time up to *overzoom_max* (the enlarged-ancestor
    range). With no cache at all, the full 2..*fallback_max* range applies.
    Returns *current* unchanged when a press would do nothing."""
    zs = list(zooms) or list(range(2, fallback_max + 1))
    nxt = step_zoom(zs, current, direction)
    if direction > 0 and nxt == current and current < overzoom_max:
        return current + 1            # past the cached edge: overzoom renders it
    return nxt


def can_zoom(zooms: List[int], current: int, direction: int,
             overzoom_max: int, fallback_max: int) -> bool:
    """True when a press in *direction* changes the level — so a button can
    grey out instead of silently doing nothing."""
    return next_zoom(zooms, current, direction, overzoom_max, fallback_max) != current


def render_zoom(zooms: List[int], z: int, overzoom_max: int) -> int:
    """The level the pane actually DRAWS for a requested *z*: snapped down to
    a cached level below the edge (no level with no tiles), capped at the
    overzoom allowance above it. Unchanged when nothing is cached."""
    if not zooms or z <= zooms[-1]:
        return snap_zoom(list(zooms), z)
    return min(z, overzoom_max)


def centre_of(view) -> Tuple[float, float]:
    """The geo point under the middle pixel of a drawn MercatorView — what
    the crosshair sits on, and what 'Select here' adopts."""
    return view.to_latlon(view.width / 2.0, view.height / 2.0)


def format_pin(lat: float, lon: float) -> str:
    """The ONE way coordinates are printed on the confirm screen. Six
    decimals (~0.1 m) — the screen never shows a rounder number than the
    certificate receives."""
    return f"{lat:.6f}, {lon:.6f}"


def tile_report(placements: Iterable, textures, misses) -> Tuple[int, int]:
    """(present, total) for the tiles a view asked for, judged from what the
    drawer has ALREADY resolved — its texture cache (hits) and its remembered
    absences (misses). No SQLite, no network: this reads the drawer's own
    record of the last draw."""
    present = total = 0
    for t in placements:
        total += 1
        key = (t.z, t.x, t.y)
        if key in textures and key not in misses:
            present += 1
    return present, total


def tile_caption(zoom: Optional[int], present: int, total: int,
                 zooms: List[int]) -> str:
    """One honest line under the map about the level on screen.

    The three states the operator can be in, kept visibly distinct (the
    'never-mentioned is grey, not amber' rule): a level whose tiles are all
    carried; a level the cache has nowhere near here, drawn enlarged from a
    wider one; and no carried map at all — where zoom and pick cannot work
    and the other two roads (typed address, GPS) are named instead."""
    if not zooms or zoom is None:
        return tr("No offline map carried — type an address or use GPS; "
                  "there is nothing to zoom or pick from.")
    if total and present == total:
        return tr("Zoom {z} · all {n} tiles here carried").format(z=zoom, n=total)
    if present == 0:
        return tr("Zoom {z} · no tiles carried this close — picture enlarged "
                  "from a wider zoom (soft)").format(z=zoom)
    return tr("Zoom {z} · {p} of {n} tiles here carried, the rest enlarged "
              "(soft)").format(z=zoom, p=present, n=total)
