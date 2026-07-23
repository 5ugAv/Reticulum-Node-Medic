"""SCAN overlay pure helpers — mesh link segments + suggestion markers.

The map WIDGET is Kivy (not exercised in CI). These tests cover only the pure
logic extracted into ``ui.screens.scan_screen``: turning a topology into drawable
line segments and normalising placement suggestions. So the tests run with or
without Kivy installed, we stub the Kivy modules ``scan_screen`` imports at load
time before importing it (the helpers touch no Kivy at all)."""

import sys
import types

import pytest


def _install_kivy_stubs():
    """Register featherweight stand-ins for every Kivy submodule scan_screen (and
    its ui.onscreen_keyboard import) pulls in, so importing the module never needs
    a real Kivy / display. Only class bases (Widget/BoxLayout/...) execute at
    import; a permissive dummy class satisfies both 'subclass this' and 'call
    this'."""
    class _Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Dummy()

    def _module(name):
        m = types.ModuleType(name)
        m.__getattr__ = lambda attr: _Dummy      # any symbol -> the dummy class
        return m

    for name in (
        "kivy", "kivy.clock", "kivy.core", "kivy.core.image", "kivy.core.window",
        "kivy.graphics", "kivy.metrics", "kivy.app", "kivy.uix",
        "kivy.uix.boxlayout", "kivy.uix.button", "kivy.uix.floatlayout",
        "kivy.uix.label", "kivy.uix.textinput", "kivy.uix.widget",
        "kivy.uix.popup",
    ):
        sys.modules.setdefault(name, _module(name))


_install_kivy_stubs()

from ui.screens.scan_screen import link_segments, suggestion_markers  # noqa: E402
from monitor.topology import build_topology, MEDIC_ID  # noqa: E402
from monitor.placement import Suggestion  # noqa: E402
from monitor.registry import NodeRegistry  # noqa: E402
from monitor.health_beacon import encode, decode  # noqa: E402

NOW = 1_000_000.0


def _beacon(rssi=-70):
    return decode(encode(uptime_s=100, heap_kb=140, wifi_rssi_dbm=rssi,
                         reset_reason=0, wifi_up=True, lora_up=True,
                         tcp_backbone_up=True, local_tcp_server_up=True,
                         wdt_armed=True, psram=True, fault=False,
                         board_id=0x3F, fw=(0, 6, 2)))


def _registry():
    r = NodeRegistry()
    r.register("aaaa", name="Northcote", lat=-37.770, lon=145.000)
    r.register("bbbb", name="Thornbury", lat=-37.756, lon=145.005)
    r.register("cccc", name="Coburg", lat=-37.744, lon=144.965)
    r.ingest("aaaa", _beacon(-70), now=NOW)
    r.ingest("bbbb", _beacon(-95), now=NOW)
    return r


# ---- link_segments -----------------------------------------------------------

def test_link_segments_skips_unlocated_endpoints():
    # medic itself has no lat/lon, so medic<->node edges yield no line.
    topo = build_topology(_registry(), paths=[], now=NOW)
    segs = link_segments(topo)
    assert segs == []                       # only medic-edges exist, medic unplaced


def test_link_segments_draws_line_between_two_located_nodes():
    # a path via aaaa to cccc creates the located aaaa<->cccc link.
    paths = [{"hash": "cccc", "via": "aaaa", "hops": 2}]
    topo = build_topology(_registry(), paths, now=NOW)
    segs = link_segments(topo)
    assert len(segs) == 1
    lat1, lon1, lat2, lon2 = segs[0]
    ends = {(round(lat1, 3), round(lon1, 3)), (round(lat2, 3), round(lon2, 3))}
    assert ends == {(-37.770, 145.000), (-37.744, 144.965)}


def test_link_segments_dedups_reversed_pairs():
    class _N:
        def __init__(self, nid, lat, lon):
            self.id, self.lat, self.lon = nid, lat, lon

    class _E:
        def __init__(self, a, b):
            self.a, self.b = a, b

    class _T:
        nodes = [_N("x", 1.0, 2.0), _N("y", 3.0, 4.0)]
        edges = [_E("x", "y"), _E("y", "x")]          # same link, both directions

    assert len(link_segments(_T())) == 1


def test_link_segments_handles_empty_topology():
    class _T:
        nodes = []
        edges = []
    assert link_segments(_T()) == []


# ---- suggestion_markers ------------------------------------------------------

def test_suggestion_markers_from_placement_objects():
    sugs = [
        Suggestion(kind="fill_gap", lat=-37.76, lon=145.0, reason="bridge A and B"),
        Suggestion(kind="extend_reach", lat=-37.75, lon=144.98, reason="extend past C"),
    ]
    out = suggestion_markers(sugs)
    assert out == [
        {"lat": -37.76, "lon": 145.0, "reason": "bridge A and B", "kind": "fill_gap"},
        {"lat": -37.75, "lon": 144.98, "reason": "extend past C",
         "kind": "extend_reach"},
    ]


def test_suggestion_markers_accepts_dicts():
    out = suggestion_markers([{"lat": 1.0, "lon": 2.0, "reason": "r", "kind": "k"}])
    assert out == [{"lat": 1.0, "lon": 2.0, "reason": "r", "kind": "k"}]


def test_suggestion_markers_drops_uncoordinated_and_dedups():
    sugs = [
        {"lat": None, "lon": 2.0, "reason": "no lat", "kind": "fill_gap"},
        {"lat": 1.0, "lon": 2.0, "reason": "keep", "kind": "fill_gap"},
        {"lat": 1.0, "lon": 2.0, "reason": "dup", "kind": "fill_gap"},
        {"lat": 1.0, "lon": 2.0, "reason": "diff kind", "kind": "extend_reach"},
    ]
    out = suggestion_markers(sugs)
    assert len(out) == 2
    assert out[0]["reason"] == "keep"


def test_suggestion_markers_empty_and_none():
    assert suggestion_markers([]) == []
    assert suggestion_markers(None) == []
