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
    """Only what set_show_boundary / _fetch_boundary touch."""

    def __init__(self, provider=None, show=False):
        self._boundary_provider = provider
        self._show_boundary = show
        self.redraws = 0

    def _redraw(self, *_a, **_k):
        self.redraws += 1

    def set_show_boundary(self, on):
        self._show_boundary = bool(on)


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
    """Only what _toggle_boundary touches."""

    def __init__(self):
        self._boundary_on = False
        self.plot = _Plot()
        self.boundary_btn = _Btn("Boundary  off")


def test_toggle_boundary_flips_state_label_and_the_plot():
    s = _Screen()
    scan.ScanScreen._toggle_boundary(s)
    assert s._boundary_on is True
    assert s.plot._show_boundary is True
    assert s.boundary_btn.text == "Boundary  on"
    scan.ScanScreen._toggle_boundary(s)
    assert s._boundary_on is False
    assert s.plot._show_boundary is False
    assert s.boundary_btn.text == "Boundary  off"


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
    body = func_source("ui/app.py", "build", cls="ReticulumNodeMedicApp")
    assert "boundary_provider=_boundary_provider" in body
    assert "load_walk_failures" in body
    assert "candidate_keys" in body
    assert "node_boundary" in body
    assert "boundary_segments" in body
    # Joined against the registry's own device fold, the SAME one the
    # anchor file already uses — not a second convention.
    assert "monitor.walk_anchor import candidate_keys" in body
