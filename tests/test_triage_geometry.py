"""Bullseye geometry — pure layout maths, no Kivy."""

import pytest

from ui.triage_geometry import bullseye_geometry, dot_position, RING_STOPS


def test_centres_on_the_canvas():
    g = bullseye_geometry(400, 800)          # portrait
    assert (g["cx"], g["cy"]) == (200, 400)


def test_sizes_to_the_smaller_dimension_in_both_orientations():
    portrait = bullseye_geometry(480, 800)
    landscape = bullseye_geometry(800, 480)
    # both fit the 480 dimension, minus the margin
    assert portrait["max_r"] == pytest.approx(landscape["max_r"], abs=1e-6)
    assert portrait["max_r"] == pytest.approx((480 / 2) * (1 - 0.08), abs=1e-6)


def test_rings_run_outer_to_inner_with_decreasing_radius():
    g = bullseye_geometry(600, 600)
    radii = [r for r, _, _ in g["rings"]]
    assert radii == sorted(radii, reverse=True)      # outer (largest) first
    assert len(g["rings"]) == len(RING_STOPS)
    assert g["rings"][0][1] == "freezing" and g["rings"][-1][1] == "bullseye"
    assert g["rings"][0][0] == pytest.approx(g["max_r"], abs=1e-6)


def test_dot_at_centre_when_hot_and_edge_when_freezing():
    g = bullseye_geometry(500, 500)
    centre = dot_position(0.0, g)            # score 1.0 -> radius 0
    assert centre == pytest.approx((g["cx"], g["cy"]), abs=1e-6)
    edge = dot_position(1.0, g)              # score 0.0 -> outer edge
    dist = ((edge[0] - g["cx"]) ** 2 + (edge[1] - g["cy"]) ** 2) ** 0.5
    assert dist == pytest.approx(g["max_r"], abs=1e-6)


def test_dot_radius_clamps():
    g = bullseye_geometry(500, 500)
    assert dot_position(-1.0, g) == dot_position(0.0, g)
    assert dot_position(2.0, g) == dot_position(1.0, g)


def test_dot_moves_inward_as_score_improves():
    g = bullseye_geometry(500, 500)
    # dot_radius = 1 - score, so a better score -> smaller radius -> closer to centre
    far = dot_position(0.8, g)     # poor score
    near = dot_position(0.2, g)    # good score
    d_far = ((far[0] - g["cx"]) ** 2 + (far[1] - g["cy"]) ** 2) ** 0.5
    d_near = ((near[0] - g["cx"]) ** 2 + (near[1] - g["cy"]) ** 2) ** 0.5
    assert d_near < d_far
