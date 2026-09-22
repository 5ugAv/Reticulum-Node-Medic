"""The thanks page names what the code actually fetches (operator,
2026-09-21: "do we need to adjust the maps?" — yes: the basemap had moved
from Carto to Esri a month earlier and the credit still said Carto)."""
import ast
import re

from ui import map_download

# The credits screen imports Kivy; CI has none. Read the CREDITS list out of
# the source instead — it is a literal, and a literal is what we are checking.
_SRC = open("ui/screens/credits_screen.py").read()
_CREDITS = ast.literal_eval(re.search(r"^CREDITS = (\[.*?^\])", _SRC, re.S | re.M).group(1))


def _credit(role):
    return dict(_CREDITS)[role]


def test_map_credit_matches_the_basemap_and_the_geocoder():
    maps = _credit("Maps")
    if "arcgisonline.com" in map_download.OSM_URL:
        assert "Esri" in maps
    if "openstreetmap" in map_download.OSM_URL or "OpenStreetMap" in map_download.ATTRIBUTION:
        assert "OpenStreetMap" in maps
    assert "CARTO" not in maps.upper() or "carto" in map_download.OSM_URL


def test_terrain_credit_matches_the_terrain_source():
    terrain = _credit("Terrain")
    if "elevation-tiles-prod" in map_download.TERRAIN_URL:
        assert "Tilezen" in terrain and "AWS" in terrain


def test_no_engineering_companion_line():
    assert all(role != "Engineering companion" for role, _ in _CREDITS)
