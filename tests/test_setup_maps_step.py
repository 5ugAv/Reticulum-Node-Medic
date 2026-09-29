"""The setup wizard tells a builder to put maps on the medic (2026-09-29).

Operator: "if someone's building a Node Medic from the GitHub repo, they'll
need to download all the resources — that should happen during setup, not on
the screen." The step sits with the MAPS tour screen, opens MAPS, and its
sentence about what is carried comes from the SQLite the map draws from.
"""
from ui import setup_flow as F


def test_the_maps_step_follows_the_maps_tour_screen():
    keys = [s["key"] for s in F.setup_steps()]
    assert keys.index(F.TOUR_SCAN) < keys.index(F.TOUR_MAPS_DOWNLOAD) < keys.index(F.TOUR_TRIAGE)


def test_the_step_opens_maps_where_the_download_actually_lives():
    step = [s for s in F.setup_steps() if s["key"] == F.TOUR_MAPS_DOWNLOAD][0]
    assert step["opens"] == "scan"
    assert "Wi-Fi" in step["body"], "it must say the download needs a connection"
    assert "{summary}" not in step["body"], "the summary is filled in, never shown raw"


def test_a_fresh_build_is_told_it_carries_nothing():
    s = F.maps_summary_sentence({"tiles": 0, "zmin": None, "zmax": None, "terrain": False})
    assert "NO maps" in s and "blank" in s


def test_a_carried_area_is_described_from_what_is_on_disk():
    s = F.maps_summary_sentence({"tiles": 90193, "zmin": 0, "zmax": 15, "terrain": True})
    assert "90,193" in s and "zoom 0–15" in s and "with terrain" in s


def test_a_basemap_without_terrain_says_so():
    s = F.maps_summary_sentence({"tiles": 12, "zmin": 8, "zmax": 12, "terrain": False})
    assert "no terrain" in s


def test_the_summary_never_raises_when_the_map_store_is_unreadable(monkeypatch):
    """A broken SQLite must cost a sentence, never the wizard."""
    import ui.map_download as md
    monkeypatch.setattr(md, "carried_summary", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert "NO maps" in F.maps_summary_sentence()
