"""SYNAPSE phase 5 — the recommender folds into SCAN (no seventh mode).

Pure logic only, per house convention: the pins' marker dicts learn to carry
the full §3.5 rationale, the tap popup's text is built by a testable function,
and the "Build next" panel's three lines are computed here — the Kivy widget
just renders them. The map stays the hero: the panel is collapsed by default,
and that default is pinned by source inspection.
"""

import sys
import types


def _install_kivy_stubs():
    """Featherweight Kivy stand-ins so scan_screen imports without a display —
    same pattern as tests/test_scan_lines.py."""
    class _Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Dummy()

    def _module(name):
        m = types.ModuleType(name)
        m.__getattr__ = lambda attr: _Dummy
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

from monitor.synapse_recommend import scan_recommendations  # noqa: E402
from monitor.registry import NodeRegistry  # noqa: E402
from monitor.topology import Topology, TopoNode, TopoEdge  # noqa: E402
from ui.screens.scan_screen import (  # noqa: E402
    build_next_lines,
    rationale_text,
    recommendation_markers,
    suggestion_markers,
)
from tests.srcutil import src, func_source  # noqa: E402

NOW = 1_000_000.0


def _topo(nodes, edges):
    return Topology(
        nodes=[TopoNode(id=i, name=i, lat=lat, lon=lon)
               for i, lat, lon in nodes],
        edges=[TopoEdge(a, b, transport=t, rssi=r) for a, b, t, r in edges],
        generated_at=NOW)


def _marginal_topo():
    # Two KIN nodes (roster) with a measured weak LoRa link between them.
    return _topo([("pp", 0.0, 0.0), ("wk", 0.0, 0.02)],
                 [("pp", "wk", "lora", -112)])


def _kin_registry(*hashes):
    reg = NodeRegistry()
    reg.set_kin_roster({h: {"type": "pi_propagation" if h == "pp" else "pi",
                            "name": h} for h in hashes})
    return reg


def _markers(topo, registry=None):
    recs = scan_recommendations(topo, registry=registry)
    positions = {n.id: (n.lat, n.lon) for n in topo.nodes
                 if n.lat is not None and n.lon is not None}
    return recommendation_markers(recs, positions)


# ---- one spine end to end: SCAN pins come from recommend() -------------------

def test_scan_recommendations_run_on_the_lora_first_view():
    # Two located nodes "linked" only by wifi: a LoRa gap. The engine must
    # see it — same rule the suggest() adapter enforces, one spine for both.
    topo = _topo([("aaaa", 0.0, 0.0), ("bbbb", 0.0, 0.018)],
                 [("aaaa", "bbbb", "wifi", -50)])
    recs = scan_recommendations(topo)
    assert any(r.action == "new_node" for r in recs)


def test_scan_recommendations_take_the_roster_from_the_registry():
    recs = scan_recommendations(_marginal_topo(),
                                registry=_kin_registry("pp", "wk"))
    assert any(r.action == "raise_antenna" for r in recs)   # kin -> mast advice


# ---- markers: the existing pin plumbing learns the rationale -----------------

def test_new_node_markers_carry_the_full_rationale():
    topo = _topo([("aaaa", 0.0, 0.0), ("bbbb", 0.0, 0.018)], [])
    m = [x for x in _markers(topo) if x["action"] == "new_node"][0]
    assert m["lat"] is not None and m["lon"] is not None
    assert m["kind"] == "new_node" and m["reason"]
    assert m["tier"] in ("transport", "propagation") and m["tier_why"]
    assert m["tier_checked"] in (True, False)
    assert m["resolves"] and m["cost_note"]
    assert m["links"] and all(
        {"name", "km", "margin_km", "source", "confidence"} <= set(l)
        for l in m["links"])
    assert isinstance(m["cautions"], list)


def test_raise_markers_sit_at_the_kin_node_they_name():
    ms = _markers(_marginal_topo(), registry=_kin_registry("pp", "wk"))
    raises = [x for x in ms if x["action"] == "raise_antenna"]
    assert raises
    r = raises[0]
    assert r["node"] in ("pp", "wk")
    assert (r["lat"], r["lon"]) in ((0.0, 0.0), (0.0, 0.02))   # at the node
    assert r["height_m"] == 10.0 and r["predicted_gain_db"] == 6.0


def test_a_raise_whose_node_has_no_position_stays_off_the_map():
    recs = scan_recommendations(_marginal_topo(),
                                registry=_kin_registry("pp", "wk"))
    ms = recommendation_markers(recs, positions={})            # nobody located
    assert all(x["action"] != "raise_antenna" for x in ms)


def test_suggestion_markers_preserve_rationale_extras():
    # MapPlot re-normalises provider output through suggestion_markers; the
    # rationale fields must survive that pass, not be stripped to four keys.
    out = suggestion_markers([{"lat": 1.0, "lon": 2.0, "reason": "r",
                               "kind": "new_node", "tier": "transport",
                               "links": [{"name": "A"}]}])
    assert out[0]["tier"] == "transport" and out[0]["links"] == [{"name": "A"}]


# ---- the tap popup: §3.5 rationale as plain text -----------------------------

def test_rationale_text_names_tier_links_sources_and_cost():
    topo = _topo([("aaaa", 0.0, 0.0), ("bbbb", 0.0, 0.018)], [])
    m = [x for x in _markers(topo) if x["action"] == "new_node"][0]
    text = rationale_text(m)
    assert m["tier"] in text and m["tier_why"] in text
    assert "could not check" not in text                       # it WAS checked
    assert "km" in text and "(default" in text                 # source named
    assert m["cost_note"] in text
    for line in m["resolves"]:
        assert line in text


def test_rationale_text_says_could_not_check_when_unchecked():
    text = rationale_text({"action": "new_node", "tier": "transport",
                           "tier_why": "why", "tier_checked": False,
                           "reason": "r", "resolves": [], "links": [],
                           "cost_note": "", "alternatives": [], "cautions": []})
    assert "could not check" in text.lower()


def test_rationale_text_keeps_metres_and_decibels_apart():
    ms = _markers(_marginal_topo(), registry=_kin_registry("pp", "wk"))
    r = [x for x in ms if x["action"] == "raise_antenna"][0]
    text = rationale_text(r)
    assert "10 m" in text and "6 dB" in text


def test_rationale_text_lists_cheaper_alternatives_and_cautions():
    text = rationale_text({"action": "new_node", "tier": "transport",
                           "tier_why": "w", "tier_checked": True,
                           "reason": "r", "resolves": ["fixes X"],
                           "links": [], "cost_note": "cheap",
                           "alternatives": ["Raise the antenna at A"],
                           "cautions": ["No terrain map cached"]})
    assert "Raise the antenna at A" in text
    assert "No terrain map cached" in text


# ---- the Build next panel: three single lines, severity order ----------------

def test_build_next_lines_are_at_most_three_single_lines():
    ms = _markers(_marginal_topo(), registry=_kin_registry("pp", "wk"))
    lines = build_next_lines(ms)
    assert 1 <= len(lines) <= 3
    assert all("\n" not in ln for ln in lines)


def test_build_next_puts_the_cheap_fix_first():
    ms = _markers(_marginal_topo(), registry=_kin_registry("pp", "wk"))
    lines = build_next_lines(ms)
    assert lines[0].startswith("Raise the antenna")            # mast beats node
    assert any(ln.startswith("New transport node near ") for ln in lines)


def test_build_next_names_the_nearest_node_not_raw_coordinates():
    topo = _topo([("aaaa", 0.0, 0.0), ("bbbb", 0.0, 0.018)], [])
    lines = build_next_lines(_markers(topo))
    new_lines = [l for l in lines if l.startswith("New ")]
    assert new_lines and ("aaaa" in new_lines[0] or "bbbb" in new_lines[0])


def test_build_next_of_nothing_is_empty():
    assert build_next_lines([]) == []
    assert build_next_lines(None) == []


# ---- the screen follows the operator's taste (source-inspected) --------------

def test_the_panel_is_collapsed_by_default():
    text = src("ui/screens/scan_screen.py")
    assert "_bn_expanded = False" in text                      # map stays hero
    assert "Build next" in text


def test_raise_pins_draw_as_up_arrows_not_rings():
    fn = func_source("ui/screens/scan_screen.py", "_draw_suggestions",
                     cls="MapPlot")
    assert "raise_antenna" in fn
    assert "continue" in fn                                    # skips the ring


def test_the_tap_popup_uses_the_rationale_builder():
    fn = func_source("ui/screens/scan_screen.py", "_show_suggestion",
                     cls="MapPlot")
    assert "rationale_text" in fn


def test_the_app_feeds_scan_from_the_one_spine():
    text = src("ui/app.py")
    assert "scan_recommendations(" in text
    assert "recommendation_markers(" in text
    assert "suggestion_markers(suggest(" not in text           # old path gone
