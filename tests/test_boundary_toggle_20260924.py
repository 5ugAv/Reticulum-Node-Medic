"""SCAN's Boundary toggle (operator, 2026-09-24): "Using the data from the
boundary walk ... a thin line around a node that dictates its boundary edge
on the map. Switchable." And, correcting the colour a moment later: "not
necessarily green, whatever colour the node is, the line around it should
be the same colour as the node."

Kivy has no window provider on this Mac (screens cannot be instantiated).
Covers the same two ways the rest of this SCAN-drawing suite does:

* PURE LOGIC run directly, unbound, against a featherweight stand-in
  carrying only the attributes each method touches — the ``test_terrain_
  toggle`` pattern (``MapPlot.set_show_boundary`` / ``_fetch_boundary``,
  ``ScanScreen._toggle_boundary``), under the process-global Kivy stubs
  ``test_scan_lines`` installs.
* SOURCE PINS (AST-located, via ``tests.srcutil.func_source``) for the
  header button's binding, the draw call site inside ``_draw_tiled``
  (same layer as the mesh-lines under the dots), ``_draw_boundary`` itself
  reading the node's own status colour rather than a fixed one, the
  constructor plumbing, and the real provider wired in ``ui/app.py``.

Plus the catalog coverage for the two new wrapped strings.
"""

import json
import os

import pytest

from tests.srcutil import ROOT, func_source, src

from tests.test_scan_lines import _install_kivy_stubs

_install_kivy_stubs()

import ui.screens.scan_screen as scan  # noqa: E402

SCREEN = "ui/screens/scan_screen.py"

_CATALOGS = ("de", "es", "fr", "id", "ja", "pl", "ru", "sv")


# ---- i18n -------------------------------------------------------------------

def test_boundary_toggle_strings_are_in_every_catalog():
    for lang in _CATALOGS:
        d = json.load(open(os.path.join(ROOT, f"assets/i18n/{lang}.json"),
                           encoding="utf-8"))
        assert "Boundary  off" in d, lang
        assert "Boundary  on" in d, lang
        # Two-space-before-off/on, the same style "Links  off"/"Terrain  off"
        # already use in every one of these catalogs.
        assert "  " in d["Boundary  off"] or d["Boundary  off"] != "Boundary  off", lang


# ---- MapPlot: pure toggle/fetch logic ---------------------------------------

class _Plot:
    """Only what set_show_boundary / _fetch_boundary touch. set_show_boundary
    is the SHIPPED MapPlot.set_show_boundary, bound onto the stub (2026-09-24
    review fix #16) rather than reimplemented here — the same
    test_terrain_toggle_20260814 / test_port_moves_after_flash pattern
    (``real_method.__get__(self)``). A stub's own diverging copy could drift
    from production (this one had: it never called ``_redraw``) and every
    test that goes through it — including ``ScanScreen._toggle_boundary``,
    below — would keep passing while the real method broke."""

    def __init__(self, provider=None, show=False):
        self._boundary_provider = provider
        self._show_boundary = show
        self.redraws = 0
        self.set_show_boundary = scan.MapPlot.set_show_boundary.__get__(self)
        self._fetch_boundary = scan.MapPlot._fetch_boundary.__get__(self)

    def _redraw(self, *_a, **_k):
        self.redraws += 1


def test_set_show_boundary_flips_state_and_redraws():
    p = _Plot(show=False)
    scan.MapPlot.set_show_boundary(p, True)
    assert p._show_boundary is True and p.redraws == 1
    scan.MapPlot.set_show_boundary(p, False)
    assert p._show_boundary is False and p.redraws == 2


def test_set_show_boundary_is_a_noop_when_state_is_unchanged():
    p = _Plot(show=True)
    scan.MapPlot.set_show_boundary(p, True)
    assert p.redraws == 0                     # nothing to repaint


def test_fetch_boundary_off_by_default_returns_nothing():
    p = _Plot(provider=lambda: [{"lat": 1, "lon": 1, "status": "ok",
                                 "segments": [(1, 1, 2, 2)]}], show=False)
    assert scan.MapPlot._fetch_boundary(p) == []


def test_fetch_boundary_with_no_provider_returns_nothing():
    p = _Plot(provider=None, show=True)
    assert scan.MapPlot._fetch_boundary(p) == []


def test_fetch_boundary_returns_the_providers_rings_when_on():
    rings = [{"lat": 1.0, "lon": 2.0, "status": "warn",
              "segments": [(1.0, 2.0, 1.1, 2.1)]}]
    p = _Plot(provider=lambda: rings, show=True)
    assert scan.MapPlot._fetch_boundary(p) == rings


def test_fetch_boundary_swallows_a_raising_provider():
    def _boom():
        raise RuntimeError("no walk data yet")
    p = _Plot(provider=_boom, show=True)
    assert scan.MapPlot._fetch_boundary(p) == []


# ---- ScanScreen: pure toggle logic ------------------------------------------

class _Btn:
    def __init__(self, text=""):
        self.text = text


class _Screen:
    """Only what _toggle_boundary touches. _boundary_coverage_suffix is the
    SHIPPED ScanScreen method, bound on the same way (2026-09-24 review):
    it reads self.plot._fetch_boundary(), which is itself now the real
    MapPlot method bound onto _Plot above."""

    def __init__(self, provider=None):
        self._boundary_on = False
        self.plot = _Plot(provider=provider)
        self.boundary_btn = _Btn("Boundary  off")
        self._boundary_coverage_suffix = \
            scan.ScanScreen._boundary_coverage_suffix.__get__(self)


def test_toggle_boundary_flips_state_label_and_the_plot():
    s = _Screen()
    scan.ScanScreen._toggle_boundary(s)
    assert s._boundary_on is True
    assert s.plot._show_boundary is True
    assert s.boundary_btn.text == "Boundary  on"          # no provider -> no suffix
    scan.ScanScreen._toggle_boundary(s)
    assert s._boundary_on is False
    assert s.plot._show_boundary is False
    assert s.boundary_btn.text == "Boundary  off"


def test_toggle_boundary_on_appends_real_coverage_when_a_provider_is_wired():
    """fix #13: the coverage data must stop being computed and thrown away
    — the toggle button reads it straight off what the provider returns."""
    rings = [{"lat": 1.0, "lon": 1.0, "status": "ok",
              "segments": [(1.0, 1.0, 1.1, 1.1)], "coverage_frac": 0.125,
              "total_failures_used": 3}]
    s = _Screen(provider=lambda: rings)
    scan.ScanScreen._toggle_boundary(s)
    assert s.boundary_btn.text == "Boundary  on (3/24)"
    scan.ScanScreen._toggle_boundary(s)
    assert s.boundary_btn.text == "Boundary  off"


def test_boundary_coverage_suffix_is_empty_when_nothing_is_drawn():
    s = _Screen(provider=lambda: [])
    s._boundary_on = True
    s.plot._show_boundary = True
    assert s._boundary_coverage_suffix() == ""


def test_boundary_coverage_suffix_swallows_a_raising_provider():
    def _boom():
        raise RuntimeError("no data yet")
    s = _Screen(provider=_boom)
    s._boundary_on = True
    s.plot._show_boundary = True
    assert s._boundary_coverage_suffix() == ""


# ---- source pins -------------------------------------------------------------

def test_boundary_button_exists_and_is_bound_to_the_toggle():
    body = func_source(SCREEN, "__init__", cls="ScanScreen")
    assert 'tr("Boundary  off")' in body
    assert "boundary_btn.bind" in body
    assert "_toggle_boundary" in body
    # Sits in the same header row as Links/Terrain, not a separate row.
    assert "header_row.add_widget(self.boundary_btn)" in body


def test_boundary_provider_is_threaded_into_the_map_plot_constructor():
    body = func_source(SCREEN, "__init__", cls="ScanScreen")
    assert "boundary_provider=boundary_provider" in body


def test_boundary_default_is_off():
    body = func_source(SCREEN, "__init__", cls="MapPlot")
    assert "self._show_boundary = False" in body
    body2 = func_source(SCREEN, "__init__", cls="ScanScreen")
    assert "self._boundary_on = False" in body2


def test_draw_tiled_draws_the_ring_under_the_dots_same_as_links():
    body = func_source(SCREEN, "_draw_tiled", cls="MapPlot")
    assert "self._draw_links(view)" in body
    assert "self._draw_boundary(view)" in body
    # Under the dots: both link lines and boundary rings are drawn (in the
    # open canvas context) BEFORE the node-dot loop that follows them.
    dots_idx = body.index("for p in pts:")
    assert body.index("self._draw_links(view)") < dots_idx
    assert body.index("self._draw_boundary(view)") < dots_idx


def test_draw_boundary_reads_the_nodes_own_status_colour_not_a_fixed_one():
    body = func_source(SCREEN, "_draw_boundary", cls="MapPlot")
    assert 'theme.status_rgba(ring.get("status")' in body
    assert "self._fetch_boundary()" in body
    # THIN: half _draw_links' thinnest line (dp(0.45) * 1.0 floor -> ~dp(1)
    # in practice; dp(1.2) here is the operator's "thin line" ask).
    assert "dp(1.2)" in body
    # The colour call itself reads the node's own status, never a literal
    # RGBA tuple standing in for it.
    assert "Color(*theme.status_rgba(ring" in body
    assert "Color(0." not in body and "Color(1." not in body


def test_draw_boundary_skips_rings_with_no_segments():
    body = func_source(SCREEN, "_draw_boundary", cls="MapPlot")
    assert "if not segs" in body


# ---- ui/app.py wiring --------------------------------------------------------

def test_app_wires_a_real_boundary_provider():
    """The closure in build() is now a thin shim (2026-09-24 review restructure
    — see tests/test_boundary_rings_20260924.py for the real behavioural
    coverage of the pure logic, monitor.boundary_rings.boundary_rings_for)."""
    body = func_source("ui/app.py", "build", cls="ReticulumNodeMedicApp")
    assert "boundary_provider=_boundary_provider" in body
    assert "load_walk_failures" in body
    assert "monitor.boundary_rings import boundary_rings_for" in body
    assert "boundary_rings_for(self.monitor_service.registry" in body


def test_boundary_rings_for_does_the_geometry_not_the_app_closure():
    from monitor.boundary_rings import boundary_rings_for
    import inspect
    src = inspect.getsource(boundary_rings_for)
    assert "node_boundary" in src
    assert "boundary_segments" in src
    assert "consolidated_records" in src
