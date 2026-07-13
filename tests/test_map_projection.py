import math

import pytest

from ui.map_projection import GeoPoint, Placed, project, geo_points


def test_geo_points_adapts_registry_dicts():
    [g] = geo_points([{"lat": -37.8, "lon": 144.9, "name": "FAITH",
                       "status": "alert"}])
    assert (g.lat, g.lon, g.label, g.status) == (-37.8, 144.9, "FAITH", "alert")


def test_geo_points_drops_unlocated_and_handles_empty():
    nodes = [{"name": "NOLOC"}, {"lat": -37.8, "lon": 144.9, "name": "OK"}]
    assert [g.label for g in geo_points(nodes)] == ["OK"]
    assert geo_points([]) == []
    assert geo_points(None) == []

W, H, PAD = 400.0, 300.0, 24.0


def gp(lat, lon, label="", status="ok"):
    return GeoPoint(lat=lat, lon=lon, label=label, status=status)


def test_empty_returns_empty():
    assert project([], W, H) == []


def test_single_point_is_centred():
    [pl] = project([gp(-37.8, 144.9)], W, H)
    assert pl.x == pytest.approx(W / 2)
    assert pl.y == pytest.approx(H / 2)


def test_all_identical_points_centre():
    placed = project([gp(-37.8, 144.9), gp(-37.8, 144.9)], W, H)
    assert all(p.x == pytest.approx(W / 2) and p.y == pytest.approx(H / 2)
               for p in placed)


def test_north_maps_to_higher_y():
    south, north = project([gp(-38.0, 145.0), gp(-37.0, 145.0)], W, H)
    # same longitude -> same x; higher latitude (north) -> higher y (Kivy y-up)
    assert south.x == pytest.approx(north.x)
    assert north.y > south.y


def test_east_maps_to_higher_x():
    west, east = project([gp(-37.8, 144.0), gp(-37.8, 145.0)], W, H)
    assert west.y == pytest.approx(east.y)
    assert east.x > west.x


def test_all_points_within_padded_viewport():
    pts = [gp(-37.9, 144.8), gp(-37.7, 145.2), gp(-37.82, 144.95)]
    for pl in project(pts, W, H, PAD):
        assert PAD - 0.01 <= pl.x <= W - PAD + 0.01
        assert PAD - 0.01 <= pl.y <= H - PAD + 0.01


def test_aspect_ratio_preserved_letterboxed():
    # a geographically ~square area in a WIDE viewport must pillar-box: the used
    # width < inner width, and it's centred (equal margins), not stretched.
    mean = -37.8
    kx = math.cos(math.radians(mean))
    dlon_deg = 0.1 / kx           # so dlon*kx == dlat == 0.1 -> square area
    pts = [gp(mean - 0.05, 145.0), gp(mean + 0.05, 145.0 + dlon_deg)]
    placed = project(pts, 800.0, 200.0, 0.0)   # very wide viewport
    xs = [p.x for p in placed]
    ys = [p.y for p in placed]
    used_w = max(xs) - min(xs)
    used_h = max(ys) - min(ys)
    # square area -> equal used extents (aspect preserved, not stretched to 800)
    assert used_w == pytest.approx(used_h, abs=1.0)
    # centred horizontally in the wide viewport
    assert min(xs) == pytest.approx(800.0 - max(xs), abs=1.0)


def test_longitude_is_cos_lat_compressed():
    # equal degree spans: at -60 deg lat, a lon span should render NARROWER than
    # the same lat span (cos(60)=0.5), i.e. the layout accounts for latitude.
    lat_pair = project([gp(-60.5, 10.0), gp(-59.5, 10.0)], W, H, 0.0)  # 1 deg lat
    lon_pair = project([gp(-60.0, 9.5), gp(-60.0, 10.5)], W, H, 0.0)   # 1 deg lon
    lat_span = abs(lat_pair[0].y - lat_pair[1].y)
    lon_span = abs(lon_pair[0].x - lon_pair[1].x)
    # both fill their own layout; check the ratio via a combined layout instead:
    combined = project([gp(-60.5, 10.0), gp(-59.5, 10.0),
                        gp(-60.0, 9.5), gp(-60.0, 10.5)], W, H, 0.0)
    ys = [c.y for c in combined]
    xs = [c.x for c in combined]
    # 1 deg lat spans the full lat range; 1 deg lon spans ~cos(60)=0.5 of it
    assert (max(xs) - min(xs)) == pytest.approx(0.5 * (max(ys) - min(ys)), rel=0.05)


def test_status_and_label_carried_through():
    [pl] = project([gp(-37.8, 144.9, label="FAITH", status="alert")], W, H)
    assert pl.point.label == "FAITH"
    assert pl.point.status == "alert"


def test_vertical_line_centres_horizontally():
    # all same longitude -> a vertical line, centred on x
    placed = project([gp(-38.0, 145.0), gp(-37.0, 145.0)], W, H)
    assert all(p.x == pytest.approx(W / 2) for p in placed)
